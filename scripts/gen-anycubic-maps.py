#!/usr/bin/env python3
"""Regenerate `presets/anycubic_map.json.gz` from the Anycubic Slicer Next
installed on this machine.

**Why a COMPLETE resolved config, not preset names.** A scope test proved it in
the slicer's own CLI: a 3MF that only NAMES Anycubic presets keeps its partial
config and is refused ("Relative extruder addressing requires ... G92 E0"), while
one that carries every value binds by name and slices. It is the Prusa lesson in
a second dialect. This map therefore stores VALUES for every machine, process and
filament preset Filafy can name, per printer x nozzle.

**The values come from the slicer itself, never from our own reading of the JSON.**
Each preset is loaded through the app's CLI (`--load-settings` / `--load-filaments`
then `--export-3mf`) and read back out of the project it writes. That matters
because the app NORMALISES what it loads: it drops keys it no longer knows
(`is_custom_defined`, `lattice_angle_1`), replaces enum values it does not accept
(`zig-zag` becomes `rectilinear`), and resolves per-nozzle-type temperatures
(`nozzle_temperature_BRASS`). A file carrying the raw JSON values would differ from
the preset as the app holds it, and show as "modified" or trip a substitution
notice. The raw JSON is used only to ENUMERATE presets, to resolve `inherits` for
the key SCOPE (which keys are machine, process or filament keys) and to read
`compatible_printers`. Same principle as `gen-prusa-maps.py` driving `--save`.

**What is in the map, per model x nozzle:** the machine preset name and its
values, every compatible "Standard" process preset (the quality ladder) with
its layer heights and values, and every compatible filament preset with its
values. Values are stored as a DELTA against the most common value of that key
across the whole map (`*_base`), with `null` meaning "this preset lacks the key",
which keeps the parsed map small; the server re-applies the delta.

**It validates what it reads** (the scope found Anycubic's own "Kobra 4 0.8
nozzle" naming "0.40mm Standard @Anycubic Kobra X 0.8 nozzle" as its default
process): a default process or filament that is not compatible with its machine
is replaced by the compatible Standard process nearest half the nozzle, and the
substitution is printed.

Requires Anycubic Slicer Next in /Applications. NO network. Takes a few minutes
(two CLI runs per preset are not needed; one per process and one per filament).

    python3 scripts/gen-anycubic-maps.py [--out presets] [--jobs 6]
    python3 scripts/gen-anycubic-maps.py --client   # the api.ts mirrors, from the map

Run it when Anycubic ships new profiles or an Anycubic row is added to
`stl3mf::PRINTERS`. The models and their nozzle lists are READ from that table, so
the two cannot disagree.
"""
import argparse
import collections
import concurrent.futures
import datetime
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = ROOT / "presets"
SERVER = ROOT / "server" / "src"  # Filafy's own source: not in the public presets repo
APP_DIR = Path("/Applications/AnycubicSlicerNext.app")
APP = APP_DIR / "Contents" / "MacOS" / "AnycubicSlicerNext"
PROFILES = APP_DIR / "Contents" / "Resources" / "profiles"
VENDOR = PROFILES / "Anycubic"

# Keys that describe a preset rather than configure the print. They never travel
# as config values; the project names presets through its own `*_settings_id` keys.
META = {
    "name", "inherits", "from", "instantiation", "setting_id", "type", "version",
    "compatible_printers", "compatible_printers_condition", "compatible_prints",
    "compatible_prints_condition", "renamed_from", "description", "filament_id",
    "print_settings_id", "printer_settings_id", "filament_settings_id",
    "default_print_profile", "default_filament_profile", "print_compatible_printers",
    # Present in all three kinds of preset JSON; the app drops it on load.
    "is_custom_defined",
}


def catalogue():
    """The Anycubic rows of `stl3mf::PRINTERS`: model -> nozzle list."""
    if not (SERVER / "stl3mf.rs").exists():  # the public presets repo: the map's own list
        printers = json.loads(gzip.decompress((PRESETS / "anycubic_map.json.gz").read_bytes()))["printers"]
        return [(m, [float(x) for x in e["nozzles"]]) for m, e in printers.items()]
    text = (SERVER / "stl3mf.rs").read_text()
    rows = re.findall(
        r'Printer\s*\{\s*model:\s*"([^"]+)"\s*,\s*maker:\s*MAKER_ANYCUBIC\s*,\s*'
        r'presets:\s*Presets::Anycubic\s*\{\s*nozzles:\s*&\[([^\]]*)\]', text)
    if not rows:
        sys.exit("gen-anycubic-maps: parsed ZERO Anycubic rows from stl3mf.rs, the format moved")
    return [(m, [float(x) for x in n.split(",") if x.strip()]) for m, n in rows]


def label(mm):
    s = f"{mm:.2f}".rstrip("0")
    return s[:-1] if s.endswith(".") else s


_raw = {}


def raw(kind, name):
    key = (kind, name)
    if key not in _raw:
        p = VENDOR / kind / f"{name}.json"
        _raw[key] = json.loads(p.read_text()) if p.exists() else None
    return _raw[key]


def resolved(kind, name):
    d = raw(kind, name)
    if d is None:
        return None
    out = {}
    if d.get("inherits"):
        parent = resolved(kind, d["inherits"])
        if parent is None:
            sys.exit(f"gen-anycubic-maps: {kind} {name!r} inherits missing {d['inherits']!r}")
        out.update(parent)
    out.update(d)
    return out


def instantiable(kind):
    out = []
    for p in sorted((VENDOR / kind).glob("*.json")):
        d = json.loads(p.read_text())
        if str(d.get("instantiation", "true")).lower() == "true":
            out.append(p.stem)
    return out


def compatible(kind, name, machine):
    r = resolved(kind, name)
    return machine in (r.get("compatible_printers") or [])


def export(machine, process, filament, stl, scratch):
    """The project config Anycubic Slicer Next writes for these three presets."""
    d = tempfile.mkdtemp(dir=scratch)
    try:
        out = os.path.join(d, "x.3mf")
        args = [str(APP), "--datadir", os.path.join(d, "dd"),
                "--load-settings", f"{VENDOR}/machine/{machine}.json;{VENDOR}/process/{process}.json",
                "--load-filaments", f"{VENDOR}/filament/{filament}.json",
                "--export-3mf", out, str(stl)]
        r = subprocess.run(args, capture_output=True, text=True, timeout=600)
        if not os.path.exists(out):
            tail = [l for l in (r.stdout + r.stderr).splitlines() if "error" in l.lower()][-2:]
            raise RuntimeError(f"export failed for {machine} / {process} / {filament}: {tail}")
        return json.loads(zipfile.ZipFile(out).read("Metadata/project_settings.config"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def layer(v):
    return float(v[0] if isinstance(v, list) else v)


def box_stl(path):
    """A 20 mm cube, binary STL: the CLI needs SOMETHING to export."""
    import struct
    v = [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0), (0, 0, 20), (20, 0, 20), (20, 20, 20), (0, 20, 20)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
         (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80 + struct.pack("<I", len(f)))
        for a, b, c in f:
            fh.write(struct.pack("<3f", 0, 0, 0))
            for i in (a, b, c):
                fh.write(struct.pack("<3f", *v[i]))
            fh.write(b"\0\0")


def client_blocks():
    """The two client mirrors, printed ready to paste into web/user/src/api.ts:
    `ANYCUBIC_QUALITY` (the ladder per model x nozzle) and `ANYCUBIC_BEDS`.
    Reads the COMMITTED map, so it needs no slicer. The server test
    `the_client_anycubic_tables_match_the_map` fails when the two drift."""
    m = json.loads(gzip.decompress((PRESETS / "anycubic_map.json.gz").read_bytes()))
    print("const ANYCUBIC_QUALITY: Record<string, Record<string, { heights: number[]; default: number }>> = {")
    for model, e in m["printers"].items():
        parts = []
        for nz, v in sorted(e["nozzles"].items(), key=lambda kv: float(kv[0])):
            heights = sorted({round(p["layer_mm"], 3) for p in v["processes"]})
            default = next(round(p["layer_mm"], 3) for p in v["processes"] if p["process_id"] == v["process_id"])
            parts.append(f'"{nz}": {{ heights: [{", ".join(f"{h:g}" for h in heights)}], default: {default:g} }}')
        print(f'  "{model}": {{ {", ".join(parts)} }},')
    print("};")
    print("const ANYCUBIC_BEDS: Record<string, Bed> = {")
    for model, e in m["printers"].items():
        w, d, h = e["bed"]
        print(f'  "{model}": {{ w: {w:g}, d: {d:g}, h: {h:g} }},')
    print("};")


def main():
    if "--client" in sys.argv:
        client_blocks()
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PRESETS))
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    if not APP.exists():
        sys.exit(f"gen-anycubic-maps: {APP} not found")
    index = json.loads((PROFILES / "Anycubic.json").read_text())
    app_version = None

    procs_all = instantiable("process")
    fils_all = instantiable("filament")
    scope = {"machine": set(), "process": set(), "filament": set()}

    plan = []  # (model, nozzle, machine, default_process, processes, filaments)
    for model, nozzles in catalogue():
        for mm in nozzles:
            machine = f"{model} {label(mm)} nozzle"
            m = resolved("machine", machine)
            if m is None:
                sys.exit(f"gen-anycubic-maps: no machine preset {machine!r}")
            scope["machine"] |= set(m) - META
            procs = [p for p in procs_all if compatible("process", p, machine) and " Standard @" in p]
            if not procs:
                sys.exit(f"gen-anycubic-maps: {machine} has no compatible Standard process")
            procs.sort(key=lambda p: layer(resolved("process", p)["layer_height"]))
            for p in procs:
                scope["process"] |= set(resolved("process", p)) - META
            dp = m.get("default_print_profile")
            if dp not in procs:
                want = mm / 2
                sub = min(procs, key=lambda p: abs(layer(resolved("process", p)["layer_height"]) - want))
                print(f"  {machine}: default process {dp!r} is not compatible; using {sub!r}")
                dp = sub
            fils = [f for f in fils_all if compatible("filament", f, machine)]
            if not fils:
                sys.exit(f"gen-anycubic-maps: {machine} has no compatible filament")
            for f in fils:
                scope["filament"] |= set(resolved("filament", f)) - META
            df = (m.get("default_filament_profile") or [None])[0]
            if df not in fils:
                sub = next((f for f in fils if f.startswith("Anycubic PLA @")), fils[0])
                print(f"  {machine}: default filament {df!r} is not compatible; using {sub!r}")
                df = sub
            plan.append((model, mm, machine, dp, procs, fils, df))

    overlap = (scope["machine"] & scope["process"]) | (scope["machine"] & scope["filament"]) | (scope["process"] & scope["filament"])
    if overlap:
        sys.exit(f"gen-anycubic-maps: keys in two scopes: {sorted(overlap)}")

    scratch = tempfile.mkdtemp(prefix="gen-anycubic-")
    stl = os.path.join(scratch, "box.stl")
    box_stl(stl)
    jobs = []
    for model, mm, machine, dp, procs, fils, df in plan:
        for p in procs:
            jobs.append(("p", machine, p, df))
        for f in fils:
            jobs.append(("f", machine, dp, f))
    print(f"gen-anycubic-maps: {len(plan)} machine variants, {len(jobs)} CLI exports")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(export, j[1], j[2], j[3], stl, scratch): j for j in jobs}
        for n, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            results[futs[fut]] = fut.result()
            if n % 50 == 0:
                print(f"  {n}/{len(jobs)}")
    shutil.rmtree(scratch, ignore_errors=True)

    def pick(cfg, keys, element0=False):
        out = {}
        for k in sorted(keys):
            if k not in cfg:
                continue
            v = cfg[k]
            if element0:
                if not isinstance(v, list) or len(v) != 1:
                    sys.exit(f"gen-anycubic-maps: filament key {k!r} is not a one-entry list: {v!r}")
                v = v[0]
            out[k] = v
        return out

    printers = {}
    entries = {"machine": [], "process": [], "filament": []}
    for model, mm, machine, dp, procs, fils, df in plan:
        cfg0 = results[("p", machine, dp, df)]
        app_version = app_version or cfg0.get("version")
        if cfg0.get("printer_settings_id") != machine:
            sys.exit(f"gen-anycubic-maps: {machine} exported as {cfg0.get('printer_settings_id')!r}")
        mcfg = pick(cfg0, scope["machine"])
        pentries = []
        for p in procs:
            cfg = results[("p", machine, p, df)]
            if cfg.get("print_settings_id") != p:
                sys.exit(f"gen-anycubic-maps: {p} exported as {cfg.get('print_settings_id')!r}")
            if pick(cfg, scope["machine"]) != mcfg:
                sys.exit(f"gen-anycubic-maps: {machine}'s machine values moved with the process {p}")
            pc = pick(cfg, scope["process"])
            pentries.append({
                "process_id": p,
                "layer_mm": layer(pc["layer_height"]),
                "initial_layer_mm": layer(pc["initial_layer_print_height"]),
                "config": pc,
            })
            entries["process"].append(pc)
        fentries = []
        for f in fils:
            cfg = results[("f", machine, dp, f)]
            if cfg.get("filament_settings_id") != [f]:
                sys.exit(f"gen-anycubic-maps: {f} exported as {cfg.get('filament_settings_id')!r}")
            fc = pick(cfg, scope["filament"], element0=True)
            r = resolved("filament", f)
            fentries.append({
                "name": f.split(" @")[0],
                "preset": f,
                "filament_id": r.get("filament_id", ""),
                "material": fc.get("filament_type", ""),
                "config": fc,
            })
            entries["filament"].append(fc)
        entries["machine"].append(mcfg)
        area = mcfg["printable_area"]
        xs = [float(p.split("x")[0]) for p in area]
        ys = [float(p.split("x")[1]) for p in area]
        bed = [max(xs) - min(xs), max(ys) - min(ys), layer(mcfg["printable_height"])]
        printers.setdefault(model, {"bed": bed, "nozzles": {}})
        if printers[model]["bed"] != bed:
            sys.exit(f"gen-anycubic-maps: {model}'s bed differs between nozzles")
        printers[model]["nozzles"][label(mm)] = {
            "printer_id": machine,
            "process_id": dp,
            "default_filament": df.split(" @")[0],
            "machine": mcfg,
            "processes": pentries,
            "filaments": fentries,
        }

    # Factor every scope against its most common values, so each preset stores
    # only what makes it different. `null` = this preset lacks the key.
    bases = {}
    for sc, rows in entries.items():
        base = {}
        for k in sorted(scope[sc]):
            vals = collections.Counter(json.dumps(r[k], sort_keys=True) for r in rows if k in r)
            if vals:
                base[k] = json.loads(vals.most_common(1)[0][0])
        bases[sc] = base

    def delta(cfg, base):
        d = {k: v for k, v in cfg.items() if base.get(k, object()) != v}
        d.update({k: None for k in base if k not in cfg})
        return d

    for model in printers.values():
        for n in model["nozzles"].values():
            n["machine"] = delta(n["machine"], bases["machine"])
            for p in n["processes"]:
                p["config"] = delta(p["config"], bases["process"])
            for f in n["filaments"]:
                f["config"] = delta(f["config"], bases["filament"])

    out = {
        "_comment": "GENERATED by scripts/gen-anycubic-maps.py from Anycubic Slicer Next's own exports. Do not hand-edit.",
        "source": {
            "app": f"AnycubicSlicerNext {app_version}",
            "profiles": index.get("version"),
            "generated": datetime.date.today().isoformat(),
        },
        "project_version": app_version,
        "machine_base": bases["machine"],
        "process_base": bases["process"],
        "filament_base": bases["filament"],
        "printers": printers,
    }
    blob = json.dumps(out, sort_keys=True, separators=(",", ":")).encode()
    path = Path(a.out) / "anycubic_map.json.gz"
    # mtime=0 so a regeneration from the same profiles is byte-identical.
    with open(path, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, filename="") as gz:
            gz.write(blob)
    print(f"wrote {path}: {len(blob):,} bytes JSON, {path.stat().st_size:,} gzipped; "
          f"{len(plan)} variants, {sum(len(p[4]) for p in plan)} processes, {sum(len(p[5]) for p in plan)} filaments")


if __name__ == "__main__":
    main()
