#!/usr/bin/env python3
"""Regenerate presets/prusa_printer_map.json and prusa_filament_map.json from
the PrusaSlicer vendor profiles INSTALLED on this machine.

This is the Prusa analogue of `gen-bambu-maps.py`, and it exists for the same
reason: preset names must byte-match the slicer's own or binding breaks silently
(preset maps are code + generated data, never an admin panel).

**What was measured, and why this map has to carry VALUES and not just names.**
`Metadata/Slic3r_PE.config` is loaded as a PROJECT config. PrusaSlicer seeds it
from `FullPrintConfig::defaults()` (Plater.cpp:1485), then
`PresetCollection::load_external_preset` rebuilds print, filament AND printer from
the DEFAULT preset plus whatever keys we sent, and binds to a named preset only if
`profile_print_params_same` finds them equal. So naming a preset does nothing on
its own — measured: naming a real INDX 8T's presets gave 0 tool changes, while
the preset's full resolved config gave 13. This map stores the CONFIG.

**Two decisions worth knowing before editing this script.**

1. **It reads the DATADIR copy, not the app bundle's.** PrusaSlicer updates vendor
   profiles in place, so the bundle lags: `COREONE_INDX8T` exists in the installed
   `~/Library/Application Support/PrusaSlicer/vendor/PrusaResearch.ini` and not in
   the 2.9.6 bundle. Generating from the bundle would silently omit the one printer
   we have field evidence from. The source file and its version are recorded in the
   output, the way the Bambu maps record their commit.

2. **It drives PrusaSlicer's own `--save` for the config values rather than
   serializing the INI itself.** The INI is used only to ENUMERATE models, nozzles
   and materials. Reimplementing the serializer was tried and is a trap: separators
   are per TYPE (string vectors `;` and quoted, floats `,`), scalars must not be
   replicated per extruder, and embedded newlines are escaped — a hand-rolled pass
   produced `Invalid value provided for parameter start_filament_gcode` on the
   first run. `--save` emits exactly what PrusaSlicer will read back, which is the
   same principle as the ported paint-tree decoder: never let our own writer be
   checked only by our own reader.

Requires PrusaSlicer installed locally. NO network. Usage:

    python3 scripts/gen-prusa-maps.py [--models MK4SMMU3,COREONE_INDX8T] [--out DIR]

Run it whenever PrusaSlicer ships new vendor profiles, a model is added to
`stl3mf::PRINTERS`, or Filafy's material list changes.
"""

import argparse
import collections
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = ROOT / "presets"
SERVER = ROOT / "server" / "src"  # Filafy's own source: not in the public presets repo

PRUSA_CLI = Path(
    "/Applications/Original Prusa Drivers/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"
)
DATADIR = Path.home() / "Library" / "Application Support" / "PrusaSlicer"
VENDOR_INI = DATADIR / "vendor" / "PrusaResearch.ini"

# The Prusa machines Filafy targets.
#
# **TOOLCHANGERS ARE IN:** a toolchanger is inherently capable of multi-filament
# printing, so the XL and INDX families count, and they are the most capable
# multi-colour Prusas there are.
#
# So the rule is now simply: **a Prusa that can print more than one filament
# unattended**, whether by MMU or by toolchanger. Note `XL2`/`XL2IS` are 2-tool,
# which is the MINIMUM a Filafy conversion can use (it needs two
# filaments) — they are listed because they qualify, not because they are good at
# it.
#
# Legacy MMU1/MMU2/MMU2S are still left out, and that is a SEPARATE call from the
# toolchanger one: MMU3 supersedes them on the same hardware. Worth revisiting on
# its own rather than folding into this change.
WANTED_MODELS = [
    # Toolchangers
    "COREONE_INDX8T",
    "COREONE_INDX4T",
    "XL5IS",
    "XL5",
    "XL2IS",
    "XL2",
    # NOT the XL+ (XLP2T / XLP5T, vendor bundle 2.5.8), measured on
    # 2026-10-02: PrusaSlicer resolves the stock XLP5T preset to a 2x2
    # `wiping_volumes_matrix` on a 5-tool machine, and the 2.9.6 CLI then crashes
    # in `WipeTower::prime` slicing our export (with the tower off it slices with
    # 14 tool changes).
    # MMU3
    "COREONEMMU3",
    "COREONELMMU3",
    "MK4SMMU3",
    "MK4ISMMU3",
    "MK3.9SMMU3",
    "MK3.9MMU3",
    "MK3.5MMU3",
    "MK3SMMU3",
]

# Filafy conversions are single-material by rule, so the map needs one
# preset per MATERIAL rather than per (material, type) as Bambu's does. These are
# the `filament_type` values the vendor profiles use.
WANTED_MATERIALS = ["PLA", "PETG", "ASA", "ABS", "PC", "PVB", "FLEX", "PA"]

# Keys that describe the PROFILE rather than the print, and must not be emitted
# into a project config (`compatible_*` is explicitly stripped by PrusaSlicer's own
# loader; the rest are vendor-INI bookkeeping with no config meaning).
DROP_KEYS = {
    "compatible_printers",
    "compatible_printers_condition",
    "compatible_prints",
    "compatible_prints_condition",
    "renamed_from",
    "technology",
    "family",
    "variants",
    "default_materials",
    "bed_model",
    "bed_texture",
    "thumbnail",
}


def parse_ini(path):
    """The vendor INI as {section: {key: value}}, inherits chains unresolved.

    NOTE both `#` and `;` comment a line here. Missing `;` parses commented-out
    settings as real keys named `"; bed_temperature"`, which then serialize into
    the output as garbage — it happened on the first run of this script.
    """
    secs = collections.OrderedDict()
    cur = None
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        t = line.strip()
        if not t or t.startswith("#") or t.startswith(";"):
            continue
        m = re.match(r"^\[(.+)\]$", t)
        if m:
            cur = m.group(1)
            secs.setdefault(cur, {})
            continue
        if cur is not None and "=" in line:
            k, v = line.split("=", 1)
            secs[cur][k.strip()] = v.strip()
    return secs


def resolve(secs, kind, name, seen=None):
    """A preset's full value set, walking `inherits` parents first."""
    seen = seen or set()
    key = f"{kind}:{name}"
    if key in seen or key not in secs:
        return {}
    seen.add(key)
    sec = secs[key]
    out = {}
    for parent in [p.strip() for p in sec.get("inherits", "").split(";") if p.strip()]:
        out.update(resolve(secs, kind, parent, seen))
    out.update({k: v for k, v in sec.items() if k != "inherits"})
    return out


def concrete(secs, kind):
    """Named presets of a kind, skipping `*abstract*` bases."""
    p = f"{kind}:"
    return [k[len(p) :] for k in secs if k.startswith(p) and not k.startswith(p + "*")]


def enumerate_targets(secs):
    """{model_id: {"name", "family", "printers": {variant: preset}, "print": ..., "extruders"}}"""
    models = {
        k[len("printer_model:") :]: v for k, v in secs.items() if k.startswith("printer_model:")
    }
    out = {}
    for mid in WANTED_MODELS:
        if mid not in models:
            print(f"  ! {mid} absent from {VENDOR_INI.name} — skipped", file=sys.stderr)
            continue
        rows = {}
        for p in concrete(secs, "printer"):
            r = resolve(secs, "printer", p)
            if r.get("printer_model") != mid:
                continue
            nz = [x for x in r.get("nozzle_diameter", "").split(",") if x.strip()]
            rows[r.get("printer_variant", "")] = {
                "preset": p,
                "extruders": len(nz),
                "print": r.get("default_print_profile", ""),
            }
        if not rows:
            print(f"  ! {mid} has no printer presets — skipped", file=sys.stderr)
            continue
        out[mid] = {
            "name": models[mid].get("name", mid),
            "family": models[mid].get("family", ""),
            "materials": [
                m.strip() for m in models[mid].get("default_materials", "").split(";") if m.strip()
            ],
            "variants": rows,
        }
    return out


def enable_models(datadir, targets):
    """Install every wanted model in the datadir COPY.

    PrusaSlicer only loads presets for models the user has actually installed —
    `[vendor:PrusaResearch]` in `PrusaSlicer.ini` lists them. One install
    enables only the CORE One family, so without this every MK4/MK3 MMU3 model
    queried empty and was skipped with "no PLA preset": a generator that silently
    produced a THIRD of the catalogue and exited 0.

    Safe because it writes to the copy. The live datadir is never touched — the
    app writes back to `PrusaSlicer.ini`, and the user's installed presets are the
    ground truth the measurements ran against.
    """
    ini = datadir / "PrusaSlicer.ini"
    text = ini.read_text(encoding="utf-8")
    lines = [f"model:{mid} = {'; '.join(sorted(t['variants']))}" for mid, t in targets.items()]
    block = "[vendor:PrusaResearch]\n" + "\n".join(lines) + "\n"
    if "[vendor:PrusaResearch]" in text:
        text = re.sub(r"\[vendor:PrusaResearch\]\n(?:[^\[\n][^\n]*\n?)*", block, text)
    else:
        text = text.rstrip("\n") + "\n\n" + block

    # `[filaments]` is the VISIBILITY list, and it is a second gate. Enabling the
    # models alone left MK4S/MK3 querying 7 print profiles and ZERO filament
    # profiles, because only the CORE One filaments were marked visible — and
    # `load_external_preset` requires `is_visible` too, so this is the same gate
    # that decides whether a preset can bind at all. Dropping the section makes
    # every vendor filament visible in the copy.
    text = re.sub(r"\[filaments\]\n(?:[^\[\n][^\n]*\n?)*", "[filaments]\n", text)
    ini.write_text(text, encoding="utf-8")
    print(f"  enabled {len(targets)} models (and all vendor filaments) in the datadir copy")


def query_profiles(datadir, printer):
    """PrusaSlicer's OWN answer to "what is compatible with this printer".

    `--query-print-filament-profiles` returns each compatible print profile and,
    nested under it, the filament profiles compatible with that pair.

    This replaced two guesses that were both wrong. The printer preset's
    `default_print_profile` is INHERITED, so on the CORE One MMU3 0.4 variant it
    names `0.20mm SPEED @COREONE HF0.4` — an HF profile — and PrusaSlicer refuses
    the combination outright. And `default_materials` on the printer_model is a
    suggested list, not the compatibility set. Asking the slicer is the same move
    as `--save`: the tool that enforces the rule is the one that should state it.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out = tf.name
    try:
        r = subprocess.run(
            [str(PRUSA_CLI), "--datadir", str(datadir), "--printer-profile", printer,
             "--query-print-filament-profiles", "--output", out],
            capture_output=True, text=True, timeout=180,
        )
        if not os.path.getsize(out):
            raise RuntimeError(f"query failed for {printer!r}: {r.stderr.strip()[:300]}")
        return json.load(open(out, encoding="utf-8")).get("print_profiles", [])
    finally:
        os.unlink(out)


# Print profiles that must never be picked automatically. SOLUBLE profiles
# configure a soluble-support interface material and assume a specific filament in
# a specific slot — the first run chose `0.20mm SOLUBLE FULL @MK3.5 0.4` as a
# general-purpose default, which would have shipped an art print set up for
# support material it does not have.
BAD_PROFILE_WORDS = ("soluble", "support", "draft", "vase", "test")


def pick_print_profile(profiles, preferred):
    """The print profile a Filafy export opens on.

    The model's own `default_print_profile` when PrusaSlicer agrees it is
    compatible, else the closest thing to Prusa's own default: a 0.20mm balanced
    tier. Deterministic, because it lands in the emitted config and a wobbling
    choice would make exports non-reproducible.
    """
    names = [p["name"] for p in profiles]
    ok = [n for n in names if not any(w in n.lower() for w in BAD_PROFILE_WORDS)]
    if preferred in ok:
        return preferred
    # Layer height first (0.20 is Prusa's own default tier), then a general-purpose
    # quality word. Both halves are ordered, so the pick is stable across runs.
    for height in ("0.20mm", "0.2mm", "0.25mm", "0.15mm"):
        for word in ("balanced", "quality", "structural", "speed", ""):
            for n in ok:
                if height.lower() in n.lower() and word in n.lower():
                    return n
    return ok[0] if ok else None


def filament_for(profiles, print_id, secs, material):
    """The preset for a material among those compatible with this print profile.

    Prusament over Generic when both are offered — the vendor's own tuned profile —
    then the shortest name, which prefers the plain line over its variants.
    """
    entry = next((p for p in profiles if p["name"] == print_id), None)
    if not entry:
        return None
    cands = [n for n in entry.get("filament_profiles", [])
             if resolve(secs, "filament", n).get("filament_type") == material]
    if not cands:
        return None
    cands.sort(key=lambda n: (not n.startswith("Prusament"), len(n)))
    return cands[0]


def save_config(datadir, printer, printcfg, filament, model_3mf):
    """PrusaSlicer's OWN resolved config for a preset triple, via `--save`.

    This is the whole reason the script shells out: the values come back
    serialized exactly as PrusaSlicer will read them.
    """
    with tempfile.NamedTemporaryFile(suffix=".ini", delete=False) as tf:
        out = tf.name
    try:
        cmd = [str(PRUSA_CLI), "--datadir", str(datadir)]
        # All three None = PrusaSlicer's OWN default preset, which is what
        # `load_external_preset` fills our omitted keys from.
        if printer:
            cmd += ["--printer-profile", printer, "--print-profile", printcfg,
                    "--material-profile", filament]
        cmd += ["--save", out, str(model_3mf)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if not os.path.getsize(out):
            raise RuntimeError(f"--save produced nothing for {printer!r} / {filament!r}: {r.stderr.strip()[:300]}")
        cfg = {}
        for line in open(out, encoding="utf-8"):
            if " = " not in line or line.startswith("#"):
                continue
            k, v = line.split(" = ", 1)
            k = k.strip()
            if k not in DROP_KEYS:
                cfg[k] = v.rstrip("\n")
        return cfg
    finally:
        os.unlink(out)


# `Printer { model: "Prusa CORE One MMU3", maker: MAKER_PRUSA,
#            presets: Presets::Prusa { model: "COREONEMMU3" } },`
RUST_PRUSA_ROW = re.compile(
    r'Printer\s*\{\s*model:\s*"([^"]+)"\s*,\s*maker:\s*[A-Za-z_][A-Za-z0-9_]*\s*,'
    r'\s*presets:\s*Presets::Prusa\s*\{\s*model:\s*"([^"]+)"\s*\}'
)


def gen_beds():
    """Bed W×D × height H (mm) per Prusa model, as a ready-to-paste
    `web/user/src/api.ts` PRUSA_BEDS block. Run:
    `python3 scripts/gen-prusa-maps.py --beds`.

    **Reads the ALREADY-GENERATED map, so it needs no PrusaSlicer and no network**
    — the same shape as `gen-bambu-maps.py --qualities`. The map is itself the
    vendor artifact (PrusaSlicer's own `--save` output), so the numbers still come
    from PrusaResearch.ini rather than from anyone's memory; this just projects
    two of its keys into the client's table.

    **Why the CLIENT needs its own copy at all.** The server reads `bed_shape`
    live out of the same map (`stl3mf::prusa_bed`), but the "your model may
    not fit" warning is drawn before any export exists, and an unknown model
    answers "fits" — so every Prusa owner got no build-volume warning whatsoever.
    Silent, not wrong, which is the worse kind.

    `bed_shape` is a POLYGON ("0x0,250x0,250x210,0x210"), so the footprint is its
    EXTENT — max minus min per axis, not max alone. Every vendor shape today sits
    at the origin and the two agree; the subtraction is what keeps that an
    observation rather than an assumption.
    """
    import gzip

    names = dict(
        (mid, name)
        for name, mid in [
            (m.group(1), m.group(2))
            for m in RUST_PRUSA_ROW.finditer((SERVER / "stl3mf.rs").read_text(encoding="utf-8"))
        ]
    )
    if not names:
        sys.exit("gen-prusa-maps --beds: parsed ZERO Prusa rows from stl3mf.rs — the format moved")
    with gzip.open(PRESETS / "prusa_printer_map.json.gz", "rt", encoding="utf-8") as fh:
        m = json.load(fh)
    print("== Prusa bed sizes (paste into web/user/src/api.ts PRUSA_BEDS) ==")
    # The client's table is keyed by the Filafy model NAME and must be in the same
    # order the catalogue offers, so walk stl3mf.rs's order, not the map's.
    for mid, name in names.items():
        entry = m["printers"].get(mid)
        if not entry:
            print(f"  WARNING: {name} ({mid}) is not in the generated map", file=sys.stderr)
            continue
        dims = None
        for v in entry["variants"].values():
            shape, height = v["config"].get("bed_shape"), v["config"].get("max_print_height")
            if not shape or not height:
                continue
            xs, ys = [], []
            for point in shape.split(","):
                x, _, y = point.partition("x")
                xs.append(float(x))
                ys.append(float(y))
            dims = (round(max(xs) - min(xs)), round(max(ys) - min(ys)), round(float(height)))
            break
        if dims:
            print(f'  "{name}": {{ w: {dims[0]}, d: {dims[1]}, h: {dims[2]} }},')
        else:
            print(f"  WARNING: no bed_shape/max_print_height for {name} ({mid})", file=sys.stderr)


def main():
    if "--beds" in sys.argv:
        gen_beds()
        return

    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help="comma-separated model ids (default: all WANTED_MODELS)")
    ap.add_argument("--out", default=str(PRESETS), help="output directory")
    ap.add_argument("--seed-3mf", required=True, help="any 3MF/STL for --save to load")
    # PrusaSlicer downloads a newer bundle into `cache/` before it installs
    # it into `vendor/`, so a model can exist in the cache weeks before the
    # datadir's own copy. `--vendor-ini` reads that file instead AND installs it
    # into the datadir COPY, so `--save` resolves against the same bundle.
    ap.add_argument("--vendor-ini", help="the PrusaResearch.ini to read (default: the datadir's vendor/ copy)")
    # Add `--models` to the COMMITTED maps without regenerating the rest.
    # Every other model keeps its resolved config byte for byte, so existing
    # exports do not move; only the shared `base` is recomputed.
    ap.add_argument("--merge", action="store_true", help="merge --models into the existing maps in --out")
    args = ap.parse_args()
    global VENDOR_INI
    if args.vendor_ini:
        VENDOR_INI = Path(args.vendor_ini).expanduser()

    if not PRUSA_CLI.exists():
        sys.exit(f"gen-prusa-maps: PrusaSlicer not found at {PRUSA_CLI}")
    if not VENDOR_INI.exists():
        sys.exit(f"gen-prusa-maps: vendor profiles not found at {VENDOR_INI}")

    global WANTED_MODELS
    if args.models:
        WANTED_MODELS = [m.strip() for m in args.models.split(",") if m.strip()]

    secs = parse_ini(VENDOR_INI)
    version = secs.get("vendor", {}).get("config_version", "unknown")
    print(f"gen-prusa-maps: {VENDOR_INI} (config_version {version})")

    targets = enumerate_targets(secs)
    print(f"  {len(targets)} models in scope")

    # NEVER run against the live datadir: PrusaSlicer writes back to
    # PrusaSlicer.ini, and the user's installed presets are the ground truth.
    work = tempfile.mkdtemp(prefix="gen-prusa-maps-")
    copy = Path(work) / "datadir"
    shutil.copytree(DATADIR, copy)
    if args.vendor_ini:
        shutil.copyfile(VENDOR_INI, copy / "vendor" / "PrusaResearch.ini")
        idx = VENDOR_INI.with_suffix(".idx")
        if idx.exists():
            shutil.copyfile(idx, copy / "vendor" / "PrusaResearch.idx")
    enable_models(copy, targets)
    try:
        printers, filaments = {}, {}
        for mid, model in targets.items():
            printers[mid] = {"name": model["name"], "family": model["family"], "variants": {}}
            for variant, row in sorted(model["variants"].items()):
                profiles = query_profiles(copy, row["preset"])
                print_id = pick_print_profile(profiles, row["print"])
                if not print_id:
                    print(f"  ! {mid} {variant}: no compatible print profile — skipped", file=sys.stderr)
                    continue
                base_material = "PLA"
                fil = filament_for(profiles, print_id, secs, base_material)
                if not fil:
                    print(f"  ! {mid} {variant}: no {base_material} preset — skipped", file=sys.stderr)
                    continue
                cfg = save_config(copy, row["preset"], print_id, fil, args.seed_3mf)
                # A Filafy conversion needs somewhere to put colours 2..N, so a
                # variant that resolves to ONE extruder is not a multi-material
                # machine and must not be offered. `MK3SMMU3 0.8` did exactly this:
                # it fell back to a generic single-extruder printer preset and was
                # written into the map as an "MMU3" with 1 extruder.
                extruders = len([x for x in cfg.get("nozzle_diameter", "").split(",") if x.strip()])
                if extruders < 2:
                    print(f"  ! {mid} {variant}: resolves to {extruders} extruder — not multi-material, skipped",
                          file=sys.stderr)
                    continue
                printers[mid]["variants"][variant] = {
                    "printer_id": row["preset"],
                    "print_id": print_id,
                    "extruders": extruders,
                    "config": cfg,
                }
                print(f"  {mid} {variant}: {len(cfg)} keys, {extruders} extruders, print={print_id!r}")

                # Per-material DELTA against the base config, so the map stores one
                # full config per (model, variant) and only the filament keys that
                # actually move per material.
                fam = f"{mid}/{variant}"
                filaments.setdefault(fam, {})
                for material in WANTED_MATERIALS:
                    mf = filament_for(profiles, print_id, secs, material)
                    if not mf:
                        continue
                    mcfg = save_config(copy, row["preset"], print_id, mf, args.seed_3mf)
                    delta = {k: v for k, v in mcfg.items() if cfg.get(k) != v}
                    filaments[fam][material] = {"filament_id": mf, "delta": delta}
                print(f"      materials: {', '.join(sorted(filaments[fam]))}")
        # The defaults probe belongs INSIDE this block, before the datadir goes:
        # `save_config` runs PrusaSlicer against `work`, and running it after the
        # rmtree only appears to work because PrusaSlicer fabricates a fresh datadir
        # and the no-profile default happens not to depend on one. That is luck, not
        # a contract, and it silently stops being true the moment the probe needs
        # anything the datadir holds.
        #
        # DROP EVERY KEY EQUAL TO PRUSASLICER'S OWN DEFAULT PRESET. This is not just
        # a size cut, it is the requirement stated exactly: `load_external_preset`
        # builds its comparison config from the DEFAULT preset and applies our keys
        # over it, so a key we omit is filled from the default on BOTH sides and
        # `profile_print_params_same` still matches. Only the differences have to
        # travel. The defaults come from `--save` with no profile flags, which is
        # the same 200 C / bed 0 config our export was measured collapsing to.
        defaults = save_config(copy, None, None, None, args.seed_3mf)
        for m in printers.values():
            for v in m["variants"].values():
                v["config"] = {k: val for k, val in v["config"].items() if defaults.get(k) != val}
        for fam in filaments.values():
            for ent in fam.values():
                ent["delta"] = {k: val for k, val in ent["delta"].items() if defaults.get(k) != val}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    meta = {"source": VENDOR_INI.name, "config_version": version}
    if args.merge:
        # Resolve every committed variant back to its full (default-stripped)
        # config, lay the new models over it, then re-split below.
        old_p = json.load(gzip.open(Path(args.out) / "prusa_printer_map.json.gz", "rt", encoding="utf-8"))
        old_f = json.load(gzip.open(Path(args.out) / "prusa_filament_map.json.gz", "rt", encoding="utf-8"))
        merged = {}
        for mid, m in old_p["printers"].items():
            for v in m["variants"].values():
                v["config"] = {**old_p["base"], **v["config"]}
            merged[mid] = m
        merged.update(printers)
        printers = merged
        filaments = {**old_f["families"], **filaments}
        meta = dict(old_p["meta"])
        meta["merged"] = {**meta.get("merged", {}), **{mid: {"source": VENDOR_INI.name, "config_version": version}
                                                         for mid in targets}}

    # SHARED BASE + per-variant delta. The full configs are ~376 keys each and
    # overwhelmingly identical across models, so storing them whole cost 333 KB for
    # eight variants and would have put well over a megabyte of JSON in
    # the map. Keys whose value is the same for EVERY
    # variant move to `base`; each variant keeps only what differs.
    all_cfgs = [v["config"] for m in printers.values() for v in m["variants"].values()]
    base = {}
    if all_cfgs:
        for k, v in all_cfgs[0].items():
            if all(c.get(k) == v for c in all_cfgs):
                base[k] = v
    for m in printers.values():
        for v in m["variants"].values():
            v["config"] = {k: val for k, val in v["config"].items() if base.get(k) != val}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # **GZIP is what gets committed, and the raw JSON is not.** The Prusa maps are
    # ~3.3 MB of JSON and compress ~30x. The Bambu maps stay plain JSON: at 63 KB they are diffable, someone
    # adds a printer to them by hand, and compressing them would buy nothing but a
    # second mechanism. Compression is applied where it PAYS; the per-brand lazy
    # LOADING (only touch the brand the conversion needs)
    # applies to both regardless. To inspect one: `gunzip -c presets/<name>.json.gz`.
    for name, payload in (
        ("prusa_printer_map", {"meta": meta, "base": base, "printers": printers}),
        ("prusa_filament_map", {"meta": meta, "families": filaments}),
    ):
        raw = (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode("utf-8")
        # mtime=0 so regenerating identical data produces identical BYTES — a gzip
        # header timestamp would make every run a spurious diff.
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, mtime=0,
                           fileobj=open(out / f"{name}.json.gz", "wb")) as gz:
            gz.write(raw)
        n = os.path.getsize(out / f"{name}.json.gz")
        print(f"  {name}.json.gz  {n:,} bytes (from {len(raw):,}, {len(raw)/n:.1f}x)")
    print(f"wrote the Prusa maps to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
