#!/usr/bin/env python3
"""Regenerate `presets/snapmaker_map.json.gz` from the Snapmaker Orca
installed on this machine.

**Why a COMPLETE resolved config, not preset names.** Snapmaker Orca is an
OrcaSlicer fork whose project-open path starts from its built-in defaults and adds
the file's keys, so a 3MF that only NAMES presets loses their values. An earlier scope
measured exactly that in Anycubic Slicer Next, and a first check of
today's "Other Printer" file in Snapmaker Orca 2.4.0 (2026-10-14) saw it again: a
"customized presets" dialog and a missing-G-code error. So this map stores the
VALUES of every machine, process and filament preset Filafy can name for a
Snapmaker row, per nozzle, as `gen-anycubic-maps.py` does for Anycubic.

**The values come from the profile JSON in the app bundle, resolved here, NOT
from the app's command line** (unlike `gen-anycubic-maps.py`). Measured
2026-10-13 on Snapmaker Orca 2.4.0: its CLI crashes (exit 139) on any U1 process
preset that carries `wipe_tower_filament`, and where it does not crash it ignores
`inherits` for a loaded machine file (a U1 exported as `gcode_flavor = marlin`
with 1500 mm/s^2 limits, where the preset chain says `klipper` and 20000). The
GUI builds its system presets from these same files, so the bundle is the
truth. Resolution is the app's: a file's keys over its `inherits` parent's,
recursively, with the vendor folder's `name` field as the key (several file
names differ from preset names, and a few helper files carry no name at all).
Two normalisations the app's project writer applies are applied here too, both
observed in a CLI export of a filament the CLI did load correctly:

  * every scalar is a STRING (the bundle has a few bare numbers, such as
    `extruder_clearance_height_to_lid: 140`), and
  * a filament key with one value per flow variant (`filament_flow_support:
    ["standard", "high_flow"]`) is written as the ONE value of the machine's
    variant: the U1 has no `nozzle_volume_type`, so "standard" (the app wrote
    `nozzle_temperature ["215"]` for Snapmaker PLA Matte's `["215", "220"]`).

**Names are READ, never composed** (the scope's rule): presets are listed from
the vendor folder and kept where `compatible_printers` names the machine. Two
profile traps are handled and printed:

  1. **Dangling default processes.** Every U1 machine names a default process that
     does not exist (`0.20 Standard @Snapmaker U1 (0.4 nozzle)`; the real preset
     is `0.20mm Standard @...`). The default is the process whose `renamed_from`
     names it, else the name with "mm" restored, else the ladder height nearest
     half the nozzle. The default filament (`Snapmaker PLA`) dangles the same
     way and becomes `Snapmaker PLA Basic`, else `Generic PLA`.
  2. **A UTF-8 byte-order mark** on some files in Snapmaker's repository (the
     scope saw it on `0.20mm Standard @Snapmaker U1 (0.4 nozzle).json`): every
     file is read as `utf-8-sig`.

**The quality ladder** (a layer height per rung): the "Standard" processes,
plus "High Quality" where no Standard has that height (the 0.2 nozzle's 0.08 and
0.10). "Color Mixing" and "Strength" are left out.

Requires Snapmaker Orca in /Applications. NO network, no CLI, about a second.

    python3 scripts/gen-snapmaker-maps.py [--out presets]
    python3 scripts/gen-snapmaker-maps.py --check    # exit 1 if the committed map is stale
    python3 scripts/gen-snapmaker-maps.py --client   # the api.ts mirrors, from the map

Run it when Snapmaker Orca ships new profiles or a Snapmaker row is added to
`stl3mf::PRINTERS`; the models are READ from that table. `--check` is the
staleness check: it regenerates in memory from the installed app and compares
with the committed file byte for byte (the `generated` date excluded). It needs
the app, so it is a local check, not a CI one; the cargo tests
`every_snapmaker_row_binds_a_complete_snapmaker_config` and
`the_client_snapmaker_tables_match_the_map` are the gate's half.
"""
import collections
import gzip
import json
import plistlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = ROOT / "presets"
SERVER = ROOT / "server" / "src"  # Filafy's own source: not in the public presets repo
APP_DIR = Path("/Applications/Snapmaker Orca.app")
PROFILES = APP_DIR / "Contents" / "Resources" / "profiles"
VENDOR = PROFILES / "Snapmaker"
MAP = "snapmaker_map.json.gz"

# Keys that describe a preset rather than configure the print. They never travel
# as config values; the project names presets through its own `*_settings_id` keys.
META = {
    "name", "inherits", "from", "instantiation", "setting_id", "type", "version",
    "compatible_printers", "compatible_printers_condition", "compatible_prints",
    "compatible_prints_condition", "renamed_from", "description", "filament_id",
    "print_settings_id", "printer_settings_id", "filament_settings_id",
    "default_print_profile", "default_filament_profile", "print_compatible_printers",
    "is_custom_defined",
}

# The flow variant the U1's machine presets imply: they set no
# `nozzle_volume_type`, whose default is "standard".
VARIANT = "standard"


def die(msg):
    sys.exit(f"gen-snapmaker-maps: {msg}")


def catalogue():
    """The Snapmaker rows of `stl3mf::PRINTERS`: model names (nozzles: the usual four)."""
    if not (SERVER / "stl3mf.rs").exists():  # the public presets repo: the map's own list
        return list(json.loads(gzip.decompress((PRESETS / MAP).read_bytes()))["printers"])
    text = (SERVER / "stl3mf.rs").read_text()
    rows = re.findall(
        r'Printer\s*\{\s*model:\s*"([^"]+)"\s*,\s*maker:\s*MAKER_SNAPMAKER\s*,\s*'
        r'presets:\s*Presets::Snapmaker\s*\{', text)
    if not rows:
        die("parsed ZERO Snapmaker rows from stl3mf.rs, the format moved")
    return rows


def label(mm):
    s = f"{mm:.2f}".rstrip("0")
    return s[:-1] if s.endswith(".") else s


class Bundle:
    def __init__(self, vendor):
        self.files = {}
        for kind in ("machine", "process", "filament"):
            out = {}
            for p in sorted((vendor / kind).glob("*.json")):
                d = json.loads(p.read_text(encoding="utf-8-sig"))  # trap 2: a BOM
                if not isinstance(d, dict) or not d.get("name"):
                    continue  # helper files (allow lists, colour tables)
                if d["name"] in out:
                    die(f"two {kind} files are named {d['name']!r}")
                out[d["name"]] = d
            self.files[kind] = out
        self._resolved = {}

    def resolved(self, kind, name):
        key = (kind, name)
        if key not in self._resolved:
            d = self.files[kind].get(name)
            if d is None:
                return None
            out = {}
            if d.get("inherits"):
                parent = self.resolved(kind, d["inherits"])
                if parent is None:
                    die(f"{kind} {name!r} inherits missing {d['inherits']!r}")
                out.update(parent)
            out.update(d)
            self._resolved[key] = out
        return self._resolved[key]

    def instantiable(self, kind):
        return [n for n, d in self.files[kind].items() if str(d.get("instantiation", "true")).lower() == "true"]

    def compatible(self, kind, machine):
        return [n for n in self.instantiable(kind) if machine in (self.resolved(kind, n).get("compatible_printers") or [])]


def stringify(v):
    """The app writes every value as a string, lists element by element."""
    if isinstance(v, list):
        return [stringify(x) for x in v]
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return v


def layer(v):
    return float(v[0] if isinstance(v, list) else v)


def config(resolved, scope_keys):
    return {k: stringify(resolved[k]) for k in sorted(scope_keys) if k in resolved}


def filament_values(resolved):
    """One value per key, the machine's flow variant's, as the project writes it."""
    flow = resolved.get("filament_flow_support") or [VARIANT]
    i = flow.index(VARIANT) if VARIANT in flow else 0
    out = {}
    for k in sorted(resolved):
        if k in META:
            continue
        v = stringify(resolved[k])
        if isinstance(v, list):
            if not v:
                out[k] = ""  # an empty list is "no value" (compatible_prints is META)
                continue
            v = v[i] if i < len(v) else v[0]
        out[k] = v
    return out


def ladder(bundle, procs):
    """The ladder: Standard, plus High Quality where no Standard has the height."""
    def h(p):
        return round(layer(bundle.resolved("process", p)["layer_height"]), 3)
    std = [p for p in procs if " Standard @" in p]
    std_h = {h(p) for p in std}
    hq = [p for p in procs if " High Quality @" in p and h(p) not in std_h]
    out = sorted(std + hq, key=h)
    if len({h(p) for p in out}) != len(out):
        die(f"two ladder presets share a height: {out}")
    return out


def default_process(bundle, machine, mm, rungs, named):
    if named in rungs:
        return named
    for p in rungs:
        if named in str(bundle.resolved("process", p).get("renamed_from", "")).split(";"):
            print(f"  {machine}: default process {named!r} dangles; its rename is {p!r}")
            return p
    mmed = re.sub(r"^(\d+\.\d+) ", r"\1mm ", named or "")
    if mmed in rungs:
        print(f"  {machine}: default process {named!r} dangles; using {mmed!r} (\"mm\" restored)")
        return mmed
    sub = min(rungs, key=lambda p: abs(layer(bundle.resolved("process", p)["layer_height"]) - mm / 2))
    print(f"  {machine}: default process {named!r} dangles; using {sub!r} (nearest half the nozzle)")
    return sub


def default_filament(bundle, machine, fils, named):
    if named in fils:
        return named
    for want in ("Snapmaker PLA Basic", "Generic PLA"):
        f = next((f for f in fils if f.split(" @")[0] == want), None)
        if f:
            print(f"  {machine}: default filament {named!r} dangles; using {f!r}")
            return f
    die(f"{machine} has no PLA preset to default to")


def build(vendor=VENDOR, profiles=PROFILES):
    bundle = Bundle(vendor)
    index = json.loads((profiles / "Snapmaker.json").read_text(encoding="utf-8-sig"))
    info = plistlib.loads((APP_DIR / "Contents" / "Info.plist").read_bytes())
    app_version = info.get("CFBundleShortVersionString")
    if not app_version:
        die("the app's Info.plist has no version")

    scope = {"machine": set(), "process": set(), "filament": set()}
    plan = []
    for model in catalogue():
        machines = [n for n in bundle.instantiable("machine")
                    if bundle.resolved("machine", n).get("printer_model") == model]
        if not machines:
            die(f"no machine preset has printer_model {model!r}")
        for machine in sorted(machines, key=lambda n: float(bundle.resolved("machine", n)["printer_variant"])):
            m = bundle.resolved("machine", machine)
            mm = float(m["printer_variant"])
            if {layer([x]) for x in m["nozzle_diameter"]} != {mm}:
                die(f"{machine}: nozzle_diameter {m['nozzle_diameter']} is not its variant {mm}")
            scope["machine"] |= set(m) - META
            rungs = ladder(bundle, bundle.compatible("process", machine))
            if not rungs:
                die(f"{machine} has no compatible Standard process")
            for p in rungs:
                scope["process"] |= set(bundle.resolved("process", p)) - META
            dp = default_process(bundle, machine, mm, rungs, m.get("default_print_profile"))
            fils = sorted(bundle.compatible("filament", machine))
            if not fils:
                die(f"{machine} has no compatible filament")
            for f in fils:
                scope["filament"] |= set(bundle.resolved("filament", f)) - META
            df = default_filament(bundle, machine, fils, (m.get("default_filament_profile") or [None])[0])
            plan.append((model, mm, machine, dp, rungs, fils, df))

    overlap = (scope["machine"] & scope["process"]) | (scope["machine"] & scope["filament"]) | (scope["process"] & scope["filament"])
    if overlap:
        die(f"keys in two scopes: {sorted(overlap)}")

    printers = {}
    entries = {"machine": [], "process": [], "filament": []}
    for model, mm, machine, dp, rungs, fils, df in plan:
        mcfg = config(bundle.resolved("machine", machine), scope["machine"])
        pentries = []
        for p in rungs:
            pc = config(bundle.resolved("process", p), scope["process"])
            pentries.append({
                "process_id": p,
                "layer_mm": layer(pc["layer_height"]),
                "initial_layer_mm": layer(pc["initial_layer_print_height"]),
                "config": pc,
            })
            entries["process"].append(pc)
        fentries = []
        for f in fils:
            r = bundle.resolved("filament", f)
            fc = filament_values(r)
            fentries.append({
                "name": f.split(" @")[0],
                "preset": f,
                "filament_id": stringify(r.get("filament_id", "")),
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
            die(f"{model}'s bed differs between nozzles")
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

    for m in printers.values():
        for n in m["nozzles"].values():
            n["machine"] = delta(n["machine"], bases["machine"])
            for p in n["processes"]:
                p["config"] = delta(p["config"], bases["process"])
            for f in n["filaments"]:
                f["config"] = delta(f["config"], bases["filament"])

    summary = (f"{len(plan)} variants, {sum(len(p[4]) for p in plan)} processes, "
               f"{sum(len(p[5]) for p in plan)} filaments")
    return {
        "_comment": "GENERATED by scripts/gen-snapmaker-maps.py from Snapmaker Orca's bundled profiles. Do not hand-edit.",
        "source": {
            "app": f"Snapmaker Orca {app_version}",
            "profiles": index.get("version"),
        },
        "project_version": app_version,
        "machine_base": bases["machine"],
        "process_base": bases["process"],
        "filament_base": bases["filament"],
        "printers": printers,
    }, summary


def encode(out):
    blob = json.dumps(out, sort_keys=True, separators=(",", ":")).encode()
    import io
    buf = io.BytesIO()
    # mtime=0 so a regeneration from the same profiles is byte-identical.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(blob)
    return blob, buf.getvalue()


def client_blocks():
    """The two client mirrors, printed ready to paste into web/user/src/api.ts:
    `SNAPMAKER_QUALITY` (the ladder per model x nozzle) and `SNAPMAKER_BEDS`.
    Reads the COMMITTED map, so it needs no slicer. The server test
    `the_client_snapmaker_tables_match_the_map` fails when the two drift."""
    m = json.loads(gzip.decompress((PRESETS / MAP).read_bytes()))
    print("const SNAPMAKER_QUALITY: Record<string, Record<string, { heights: number[]; default: number }>> = {")
    for model, e in m["printers"].items():
        parts = []
        for nz, v in sorted(e["nozzles"].items(), key=lambda kv: float(kv[0])):
            heights = sorted({round(p["layer_mm"], 3) for p in v["processes"]})
            default = next(round(p["layer_mm"], 3) for p in v["processes"] if p["process_id"] == v["process_id"])
            parts.append(f'"{nz}": {{ heights: [{", ".join(f"{h:g}" for h in heights)}], default: {default:g} }}')
        print(f'  "{model}": {{ {", ".join(parts)} }},')
    print("};")
    print("const SNAPMAKER_BEDS: Record<string, Bed> = {")
    for model, e in m["printers"].items():
        w, d, h = e["bed"]
        print(f'  "{model}": {{ w: {w:g}, d: {d:g}, h: {h:g} }},')
    print("};")


def main():
    args = sys.argv[1:]
    if "--client" in args:
        client_blocks()
        return 0
    if not VENDOR.is_dir():
        die(f"{VENDOR} not found (is Snapmaker Orca installed?)")
    out, summary = build()
    blob, gz = encode(out)
    path = Path(args[args.index("--out") + 1]) / MAP if "--out" in args else PRESETS / MAP
    if "--check" in args:
        committed = path.read_bytes() if path.exists() else b""
        if committed != gz:
            print(f"gen-snapmaker-maps: {path} is STALE against {out['source']['app']} "
                  f"profiles {out['source']['profiles']}: regenerate it", file=sys.stderr)
            return 1
        print(f"snapmaker map OK: {path.name} matches {out['source']['app']} profiles "
              f"{out['source']['profiles']} ({summary})")
        return 0
    path.write_bytes(gz)
    print(f"wrote {path}: {len(blob):,} bytes JSON, {len(gz):,} gzipped; {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
