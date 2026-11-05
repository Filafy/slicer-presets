#!/usr/bin/env python3
"""Regenerate the two Creality preset maps from the slicers installed on
this machine:

  * `presets/creality_map.json.gz`, Creality Print's own presets (the ★
    default slicer for a Creality printer), and
  * `presets/orca_creality_map.json.gz`, OrcaSlicer's Creality presets (the
    second slicer).

Two maps because the two apps NAME the same machine's presets differently: in
Creality Print the K2 Plus filament is `Hyper PLA @Creality K2 Plus 0.4 nozzle`
and its default process `0.20mm Standard @...`; in OrcaSlicer they are
`Creality Generic PLA @K2-all` and `0.16mm Optimal @...`. Both maps have the
`ForkMap` shape of `anycubic_map.json.gz` and `snapmaker_map.json.gz`
so one Rust reader serves all four.

**Why a COMPLETE resolved config, not preset names.** It was measured in an
Orca fork's CLI and in Snapmaker Orca's GUI: a project that only NAMES
presets loses their values. This map therefore stores VALUES for every machine,
process and filament preset Filafy can name, per printer x nozzle.

**Values from the preset chain, form from the app.** Each preset is loaded
through the app's CLI and read back, because each app NORMALISES what it loads:
it drops keys it no longer knows (`adaptive_layer_height`, `wall_infill_order`,
a filament's `bed_type`), rewrites values (`retract_before_wipe` "70" becomes
"70%", `thumbnails` "96x96,300x300" becomes "96x96/PNG, 300x300/PNG") and splits
comma-joined vectors into lists (`printable_area`, `machine_min_extruding_rate`).

  * **Creality Print 7.x has no `--export-3mf`** ("Invalid option", measured
    2026-11-03 on 7.3.0.6157). It does write the complete config it slices with
    to `$TMPDIR/full_print_config.json` on every `--slice`, typed the way a
    project writes it (lists per slot). Each preset is sliced once on a 20 mm
    cube with a private `TMPDIR`, `--datadir` and cwd, and read back from there.
  * **OrcaSlicer** writes it into `--export-3mf`'s `project_settings.config`,
    exactly as `gen-anycubic-maps.py` reads Anycubic Slicer Next.

**But both CLIs drop values a filament INHERITS** (measured 2026-11-03):
Creality Print read "eSUN ABS+ @Creality K2 Plus 0.4 nozzle" at a 200 °C
default where its `fdm_filament_abs` chain says 260, and OrcaSlicer read 485 of
its 876 Creality filaments at defaults ("CR-PLA @Ender-3 V4-all" at 200 where its
preset says 220). Their GUIs build each system preset from the whole chain (the
Snapmaker lesson). So every key the resolved preset sets takes the
PRESET's value, in the shape the app writes it (`shaped`), and the app's own form
only where the two read the same (`lenient`); the generator prints how many it
kept from the preset. Keys the app does not write are dropped; keys a preset
does not set take the app's default.

The raw bundle JSON is used only to ENUMERATE presets (by their `name` field,
never by a composed file name), to resolve `inherits` for the key SCOPE (which
keys are machine, process or filament keys) and to read `compatible_printers`.
**Guard against the CLI trap** (Snapmaker Orca's CLI ignored `inherits` on a
loaded machine file): every machine's app-read `gcode_flavor`, `nozzle_diameter`,
`printable_height` and bed must equal the bundle's resolved value, else the
generator stops.

**The rows** are `ROWS` below (this file holds them first; the server adds them
to `stl3mf::PRINTERS` and a cargo test pins the two tables together). A row
names its machine by each app's `printer_model`, which differ ("SPARKX i7" in
Creality Print, "Creality SPARKX i7" in OrcaSlicer), and OrcaSlicer 2.4.2 has no
K1C 2025 or K1 Max 2025 CFS-C preset at all: such a row is in the Creality
Print map only (`orca: None`) and offers Creality Print alone.

**The quality ladder** (one process per layer height):
  * Creality Print: the "Standard" processes, as Anycubic's.
  * OrcaSlicer: the plain quality names (`ORCA_LADDER_WORDS`: Standard,
    Optimal, Fine, Detail, Draft, ...), never HueForge, Strength, High Quality
    or the other special-purpose processes.
Two rungs at one height stop the generator.

**The preset traps, each asserted here (`check_traps`)** (the scope's list,
plus what this generator found):
  1. **K2 SE bed** 220 x 215 x 245 in both apps, not the marketed 220 x 220 x 250.
  2. **Broken default process names**: Creality Print's K2 0.2, K2 Pro 0.2 and
     Hi 0.6 machines name `" @Creality K2 0.2 nozzle"` (no layer height). The
     default is then the ladder rung nearest half the nozzle. OrcaSlicer has its
     own dangling defaults (`0.20mm Standard @Creality K1 SE 0.4`, the K1C's
     `(0.4 nozzle)` names), handled by the same rule.
  3. **Irregular names**, read verbatim and never composed:
     `0.20mm  Standard @Creality K1 SE 0.4 nozzle` (two spaces) and
     `0.1mm Standard @Creality Hi 0.2 nozzle` (no trailing zero). Layer heights
     come from the preset's `layer_height`, never from its name.
  4. **Comma-joined `printable_area`** ("0x0,350x0,350x350,0x350") in the
     bundle; the bed is read from the app's list and cross-checked against it.
  5. **A default filament that is not a list**: Creality Print's K2 Pro 0.8
     names `default_filament_profile: "Hyper PLA "` (a bare string, trailing
     space, no `@machine`). The default filament is then the variant's Hyper PLA,
     else its Generic PLA.

**Filament binding data** (`SPOOLS`, written into each map as `spools`): the
preset NAMES (the part before " @") a Filafy spool tries, most specific first.
A Creality spool takes Creality's own line ("PLA Basic" binds Hyper PLA,
decision 7 of 2026-11-02); any other brand a typed Generic, then Generic. The
Rust binder walks the list and takes the first name the bound variant carries.

**Plates**, by NAME (Creality Print's `BedType` numbers differ from Bambu's and
Orca's): each app's own menu (`PLATES`), and each model's default plate
(`default_bed_type` in its `<model>_model.json`). Creality Print's include the
"Epoxy Resin Plate"; neither app's list carries SuperTack for Filafy here.

Requires Creality Print in ~/Applications (or /Applications) and OrcaSlicer in
/Applications. NO network. About a minute per app at `--jobs 6`.

    python3 scripts/gen-creality-maps.py [--app creality|orca] [--jobs 6] [--out DIR]
    python3 scripts/gen-creality-maps.py --check     # exit 1 if a committed map is stale
    python3 scripts/gen-creality-maps.py --report    # the row and binding tables, from the maps
    python3 scripts/gen-creality-maps.py --client    # the api.ts mirrors, from the maps

Run it when either app ships new Creality profiles or a Creality row changes.
"""
import argparse
import collections
import concurrent.futures
import gzip
import io
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = ROOT / "presets"
SERVER = ROOT / "server" / "src"  # Filafy's own source: not in the public presets repo


def _first_app(*paths):
    for p in paths:
        if Path(p).is_dir():
            return Path(p)
    return Path(paths[0])


APPS = {
    "creality": {
        "label": "Creality Print",
        "dir": _first_app(Path.home() / "Applications" / "Creality Print.app", "/Applications/Creality Print.app"),
        "bin": "Contents/MacOS/CrealityPrint",
        "map": "creality_map.json.gz",
    },
    "orca": {
        "label": "OrcaSlicer",
        "dir": Path("/Applications/OrcaSlicer.app"),
        "bin": "Contents/MacOS/OrcaSlicer",
        "map": "orca_creality_map.json.gz",
    },
}

# Filafy model (member-facing: the brand prefix on every row, no underscores;
# orchestrator 2026-11-03) -> each app's own `printer_model` (Creality Print's
# "Creality K1_CFS-C", "SPARKX i7"), the color unit, and the slot
# numbers (decision 8 of 2026-11-02): every row starts on 4 colors; a full CFS
# printer (up to four units) refuses above 16, CFS Lite and every CFS-C row
# above 4. Nozzles are READ from the app (the K1 family has no 0.2, the K2 SE,
# Ender-3 V4 and every CFS-C row only 0.4).
ROWS = [
    # model,                       Creality Print,               OrcaSlicer,                unit,       slots, max
    ("Creality K2",                "Creality K2",                "Creality K2",             "CFS",       4, 16),
    ("Creality K2 Plus",           "Creality K2 Plus",           "Creality K2 Plus",        "CFS",       4, 16),
    ("Creality K2 Pro",            "Creality K2 Pro",            "Creality K2 Pro",         "CFS",       4, 16),
    ("Creality K2 SE",             "Creality K2 SE",             "Creality K2 SE",          "CFS",       4, 16),
    ("Creality Hi",                "Creality Hi",                "Creality Hi",             "CFS",       4, 16),
    ("Creality K1",                "Creality K1",                "Creality K1",             "CFS",       4, 16),
    ("Creality K1C",               "Creality K1C",               "Creality K1C",            "CFS",       4, 16),
    ("Creality K1 Max",            "Creality K1 Max",            "Creality K1 Max",         "CFS",       4, 16),
    ("Creality K1 SE",             "Creality K1 SE",             "Creality K1 SE",          "CFS",       4, 16),
    ("Creality Ender-3 V4",        "Creality Ender-3 V4",        "Creality Ender-3 V4",     "CFS",       4, 16),
    ("Creality SPARKX i7",         "SPARKX i7",                  "Creality SPARKX i7",      "CFS Lite",  4, 4),
    ("Creality K1 CFS-C",          "Creality K1_CFS-C",          "Creality K1_CFS-C",       "CFS-C",     4, 4),
    ("Creality K1C CFS-C",         "Creality K1C_CFS-C",         "Creality K1C_CFS-C",      "CFS-C",     4, 4),
    ("Creality K1C 2025 CFS-C",    "Creality K1C 2025_CFS-C",    None,                      "CFS-C",     4, 4),
    ("Creality K1 Max CFS-C",      "Creality K1 Max_CFS-C",      "Creality K1 Max_CFS-C",   "CFS-C",     4, 4),
    ("Creality K1 Max 2025 CFS-C", "Creality K1 Max 2025_CFS-C", None,                      "CFS-C",     4, 4),
    ("Creality K1 SE CFS-C",       "Creality K1 SE_CFS-C",       "Creality K1 SE_CFS-C",    "CFS-C",     4, 4),
]

# Each app's plate menu, in its own `BedType` order, by the NAME `curr_bed_type`
# takes (read from each installed binary's enum strings, 2026-11-03). "Default
# Plate" and "Customized Plate" are not offered; OrcaSlicer's SuperTack and
# Textured Cool plates are left out for Creality machines. Creality Print's window
# hides Cool and Engineering on a Creality printer, so Filafy does not offer either
# (`plates_for_printer`); they stay here because this lists what the file format takes.
PLATES = {
    "creality": ["Cool Plate", "Engineering Plate", "High Temp Plate", "Textured PEI Plate", "Epoxy Resin Plate"],
    "orca": ["Cool Plate", "Engineering Plate", "High Temp Plate", "Textured PEI Plate"],
}

# The OrcaSlicer quality words that make a ladder rung. Special-purpose
# processes (HueForge, Strength, High Quality, SpecialFilament, Spiral Vase,
# Fast, Flat Surface) are left out, as Anycubic's and Snapmaker's ladders do.
ORCA_LADDER_WORDS = {"Standard", "Optimal", "Fine", "Detail", "HighDetail", "SuperDetail",
                     "Extra Fine", "Draft", "SuperDraft", "Extra Draft"}

# Filament binding: (material, type) -> preset names (before " @") to try, most
# specific first. "creality" is a Creality-branded spool, "other" any other
# brand. A type the table lacks takes "Generic <material>-<type>", then the
# material's own row. Names are each app's own; a name a variant lacks is
# skipped, so one list serves both apps.
SPOOLS = {
    "creality": {
        ("PLA", "Basic"): ["Hyper PLA", "CR-PLA", "Creality Generic PLA", "Generic PLA"],
        ("PLA", "CF"): ["Hyper PLA-CF", "CR-PLA Carbon", "Creality Generic PLA-CF", "Generic PLA-CF"],
        ("PLA", "Matte"): ["CR-PLA Matte", "Creality Generic PLA Matte", "Generic PLA"],
        ("PLA", "Silk"): ["CR-Silk", "Creality Generic PLA Silk", "Generic PLA-Silk", "Generic PLA"],
        ("PLA", "Wood"): ["CR-Wood", "Generic PLA"],
        ("PETG", "Basic"): ["Hyper PETG", "CR-PETG", "Creality Generic PETG", "Generic PETG"],
        ("PETG", "Translucent"): ["CR-PETG", "Hyper PETG", "Creality Generic PETG", "Generic PETG"],
        ("ABS", "Basic"): ["Hyper ABS", "CR-ABS", "Creality Generic ABS", "Generic ABS"],
        ("PC", "Basic"): ["Hyper PC", "Creality Generic PC", "Generic PC"],
        ("PPA", "CF"): ["Hyper PPA-CF", "Generic PPA-CF"],
        ("TPU", "Basic"): ["CR-TPU", "HP-TPU", "Creality Generic TPU", "Generic TPU"],
        ("TPU", "Translucent"): ["CR-TPU", "HP-TPU", "Creality Generic TPU", "Generic TPU"],
    },
    # Any other brand: the typed Generic, then Generic, then Creality's own.
    "other": {
        ("PLA", "Silk"): ["Generic PLA-Silk", "Creality Generic PLA Silk"],
        ("PLA", "Matte"): ["Creality Generic PLA Matte"],
        ("PLA", "CF"): ["Generic PLA-CF", "Creality Generic PLA-CF"],
        ("PLA", "HF"): ["Creality Generic PLA High Speed", "Creality HF Generic PLA"],
        ("PETG", "CF"): ["Generic PETG-CF"],
        ("PETG", "GF"): ["Generic PETG-GF"],
        ("ABS", "GF"): ["Generic ABS-GF"],
        ("ASA", "CF"): ["Generic ASA-CF"],
        ("PA6", "CF"): ["Generic PA6-CF"],
        ("PA6", "GF"): ["Generic PA6-GF"],
        ("PA12", "CF"): ["Generic PA12-CF"],
        ("PC", "CF"): ["Generic PC-CF"],
        ("PET", "CF"): ["Generic PET-CF"],
        ("PP", "CF"): ["Generic PP-CF"],
        ("PPS", "CF"): ["Generic PPS-CF"],
    },
    # The material's plain fallback, for every brand, after the lists above.
    "material": ["Generic {m}", "Creality Generic {m}", "Creality HF Generic {m}", "Hyper {m}", "CR-{m}"],
}
# Filafy material -> the preset family it binds to (the Prusa/Anycubic aliases:
# flexibles to TPU, nylons to PA; PCTG deliberately stays PCTG).
MATERIAL_ALIAS = {"TPE": "TPU", "TPC": "TPU", "PA6": "PA", "PA11": "PA", "PA12": "PA"}

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


def die(msg):
    sys.exit(f"gen-creality-maps: {msg}")


def label(mm):
    s = f"{mm:.2f}".rstrip("0")
    return s[:-1] if s.endswith(".") else s


def layer(v):
    return float(v[0] if isinstance(v, list) else str(v).split(",")[0])


def points(v):
    """A bed outline as (x, y) floats, from a list or a comma-joined string (trap 4)."""
    items = v if isinstance(v, list) else str(v).split(",")
    out = []
    for p in items:
        x, y = p.strip().split("x")
        out.append((float(x), float(y)))
    return out


def bed_of(area, height):
    pts = points(area)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [max(xs) - min(xs), max(ys) - min(ys), layer(height)]


def app_version(key):
    info = plistlib.loads((APPS[key]["dir"] / "Contents" / "Info.plist").read_bytes())
    v = info.get("CFBundleShortVersionString")
    if not v:
        die(f"{APPS[key]['label']}'s Info.plist has no version")
    return v


def require_readable(key):
    """Refuse a vendor format this cannot read before anything runs or is
    written. OrcaSlicer 2.5 packs each vendor into a .opc with no JSON beside it."""
    profiles = APPS[key]["dir"] / "Contents" / "Resources" / "profiles"
    if (profiles / "Creality.opc").exists() and not (profiles / "Creality.json").exists():
        die(f"{APPS[key]['label']} keeps its Creality presets in a packed Creality.opc "
            "(OrcaSlicer 2.5 onward), which this generator cannot read. Filafy's OrcaSlicer "
            "data stays pinned to 2.4.2: run this with OrcaSlicer 2.4.2 installed. "
            "Nothing was written.")


class Bundle:
    """One app's Creality vendor folder, indexed by each file's `name` field."""

    def __init__(self, key):
        self.key = key
        self.profiles = APPS[key]["dir"] / "Contents" / "Resources" / "profiles"
        self.vendor = self.profiles / "Creality"
        self.index = json.loads((self.profiles / "Creality.json").read_text(encoding="utf-8-sig"))
        self.files, self.paths, self.models = {}, {}, {}
        for kind in ("machine", "process", "filament"):
            out, paths = {}, {}
            for p in sorted((self.vendor / kind).rglob("*.json")):
                d = json.loads(p.read_text(encoding="utf-8-sig"))
                if not isinstance(d, dict) or not d.get("name"):
                    continue
                if d.get("type") == "machine_model":
                    self.models[d["name"]] = d
                    continue
                if d["name"] in out:
                    die(f"{key}: two {kind} files are named {d['name']!r}")
                out[d["name"]] = d
                paths[d["name"]] = p
            self.files[kind], self.paths[kind] = out, paths
            if not out:
                die(f"{key}: no {kind} presets under {self.vendor}: an unknown bundle format?")
        self._resolved = {}

    def resolved(self, kind, name):
        k = (kind, name)
        if k not in self._resolved:
            d = self.files[kind].get(name)
            if d is None:
                return None
            out = {}
            if d.get("inherits"):
                parent = self.resolved(kind, d["inherits"])
                if parent is None:
                    die(f"{self.key}: {kind} {name!r} inherits missing {d['inherits']!r}")
                out.update(parent)
            out.update(d)
            self._resolved[k] = out
        return self._resolved[k]

    def instantiable(self, kind):
        return [n for n, d in self.files[kind].items() if str(d.get("instantiation", "true")).lower() == "true"]

    def compatible(self, kind, machine):
        return [n for n in self.instantiable(kind)
                if machine in (self.resolved(kind, n).get("compatible_printers") or [])]


def box_stl(path):
    """A 20 mm cube, binary STL: each CLI needs SOMETHING to slice or export."""
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


def app_config(key, bundle, machine, process, filament, stl, scratch):
    """The complete config the app holds for these three presets."""
    d = tempfile.mkdtemp(dir=scratch)
    try:
        b = bundle.paths
        settings = f"{b['machine'][machine]};{b['process'][process]}"
        fil = str(b["filament"][filament])
        exe = str(APPS[key]["dir"] / APPS[key]["bin"])
        if key == "creality":
            tmp = os.path.join(d, "tmp")
            os.makedirs(tmp)
            args = [exe, "--cli", "--datadir", os.path.join(d, "dd"), "--load-settings", settings,
                    "--load-filaments", fil, "--slice", "0", "--outputdir", os.path.join(d, "out"), stl]
            env = dict(os.environ, TMPDIR=tmp + "/")
            r = subprocess.run(args, cwd=d, env=env, capture_output=True, text=True, timeout=600)
            out = os.path.join(tmp, "full_print_config.json")
            if not os.path.exists(out):
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "fail" in l.lower()][-2:]
                raise RuntimeError(f"no full_print_config.json for {machine} / {process} / {filament} (rc {r.returncode}): {tail}")
            return json.loads(Path(out).read_text())
        # OrcaSlicer: --export-3mf. Its CLI range-checks every value and refuses
        # its OWN K2 0.4 and SPARKX i7 machine presets (`retraction_distances_
        # when_cut` 28 or 30 against a range of 10 to 18, OrcaSlicer 2.4.2).
        # Such a key is clamped in a flattened copy for the run and its preset
        # value put back afterwards, so the map carries what the preset says.
        clamped = {}
        for _ in range(4):
            out = os.path.join(d, "x.3mf")
            args = [exe, "--datadir", os.path.join(d, "dd"), "--load-settings", settings,
                    "--load-filaments", fil, "--export-3mf", out, stl]
            r = subprocess.run(args, cwd=d, capture_output=True, text=True, timeout=600)
            if os.path.exists(out):
                cfg = json.loads(zipfile.ZipFile(out).read("Metadata/project_settings.config"))
                for k, raw in clamped.items():
                    cfg[k] = [raw] if isinstance(cfg.get(k), list) else raw
                cfg["_clamped"] = sorted(clamped)
                return cfg
            bad = re.findall(r"^(\w+): (\S+) not in range \[([-0-9.]+),([-0-9.]+)\]", r.stdout + r.stderr, re.M)
            if not bad:
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "error" in l.lower()][-2:]
                raise RuntimeError(f"export failed for {machine} / {process} / {filament}: {tail}")
            paths = {"machine": machine, "process": process, "filament": filament}
            files = {}
            for kind, name in paths.items():
                body = dict(bundle.resolved(kind, name))
                body.pop("inherits", None)
                for k, v, lo, hi in bad:
                    if k in body:
                        raw = body[k]
                        clamped.setdefault(k, raw)
                        val = min(max(float(v), float(lo)), float(hi))
                        body[k] = f"{val:g}" if not isinstance(raw, list) else [f"{val:g}"]
                fp = os.path.join(d, f"{kind}.json")
                Path(fp).write_text(json.dumps(body))
                files[kind] = fp
            if not set(k for k, *_ in bad) <= set(clamped):
                raise RuntimeError(f"{machine}: out-of-range keys in no preset: {bad}")
            settings = f"{files['machine']};{files['process']}"
            fil = files["filament"]
        raise RuntimeError(f"{machine} / {process} / {filament}: still refused after clamping {sorted(clamped)}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def stringify(v):
    """A preset value the way a project writes it: every scalar a string."""
    if isinstance(v, list):
        return [stringify(x) for x in v]
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return v


def shaped(app, preset, element0):
    """`preset`'s value in the shape the app writes `app` in."""
    if element0 and isinstance(preset, list):
        preset = preset[0] if preset else ""
    if isinstance(app, list):
        if isinstance(preset, list):
            return stringify(preset)
        preset = stringify(preset)
        if len(app) == 1:
            return [preset]
        # A string list the app writes entry by entry: Orca's serialisation joins
        # entries with ';' and quotes each ('0,0;"\n0.2,0.4444";...', the
        # `small_area_infill_flow_compensation_model` form), a vector of numbers or
        # points with ',' ("0x0,350x0,...").
        if ";" in preset:
            def unquote(e):  # the app's C-style unescape of a quoted entry
                if len(e) > 1 and e[0] == e[-1] == '"':
                    return e[1:-1].replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
                return e
            return [unquote(e) for e in preset.split(";")]
        return preset.split(",")
    if isinstance(preset, list):
        return stringify(preset[0]) if len(preset) == 1 else ",".join(stringify(preset))
    return stringify(preset)


def lenient(v):
    """A value compared the way the app compares options, not strings: the
    formats the app normalises on load ("15" and "15%", '""' and "", the legacy
    "96x96" thumbnail and "96x96/PNG", spacing, and a multi-line list written
    as one string or as entries) read the same."""
    if isinstance(v, list):
        v = ",".join(str(x) for x in v)
    return re.sub(r'[\s%",;]|/PNG', "", str(v))


def ladder(key, bundle, procs):
    """The ladder for one variant: one process per layer height."""
    def h(p):
        return round(layer(bundle.resolved("process", p)["layer_height"]), 3)
    rungs = []
    for p in procs:
        m = re.match(r"^\d+(?:\.\d+)?mm\s+(.+?)\s+@", p)
        word = m.group(1) if m else None
        if key == "creality" and word == "Standard":
            rungs.append(p)
        elif key == "orca" and word in ORCA_LADDER_WORDS:
            rungs.append(p)
    rungs.sort(key=h)
    heights = [h(p) for p in rungs]
    if len(set(heights)) != len(heights):
        dup = [p for p in rungs if heights.count(h(p)) > 1]
        die(f"{key}: two ladder rungs share a height: {dup}")
    return rungs


def default_process(key, bundle, machine, mm, rungs, named, notes):
    if named in rungs:
        return named
    for p in rungs:
        if named and named in str(bundle.resolved("process", p).get("renamed_from", "")).split(";"):
            notes.append(f"{machine}: default process {named!r} dangles; its rename is {p!r}")
            return p
    sub = min(rungs, key=lambda p: abs(layer(bundle.resolved("process", p)["layer_height"]) - mm / 2))
    notes.append(f"{machine}: default process {named!r} is not on the ladder; using {sub!r} (nearest half the nozzle)")
    return sub


def default_filament(bundle, machine, fils, named, notes):
    if isinstance(named, list):
        named = named[0] if named else None
    if named in fils:
        return named
    for want in ("Hyper PLA", "Creality Generic PLA", "Generic PLA", "Creality HF Generic PLA"):
        f = next((f for f in fils if f.split(" @")[0] == want), None)
        if f:
            notes.append(f"{machine}: default filament {named!r} is not compatible; using {f!r}")
            return f
    die(f"{machine} has no PLA preset to default to")


def plan_app(key, bundle):
    """Enumerate every (row, nozzle) variant the map will carry."""
    notes, plan, missing = [], [], []
    scope = {"machine": set(), "process": set(), "filament": set()}
    col = 1 if key == "creality" else 2
    by_model = collections.defaultdict(list)
    for n in bundle.instantiable("machine"):
        by_model[bundle.resolved("machine", n).get("printer_model")].append(n)
    for row in ROWS:
        model, pm = row[0], row[col]
        if pm is None:
            missing.append(f"{model}: {APPS[key]['label']} has no preset for it (row offers Creality Print only)")
            continue
        machines = by_model.get(pm)
        if not machines:
            die(f"{key}: no machine preset has printer_model {pm!r} (row {model})")
        for machine in sorted(machines, key=lambda n: float(bundle.resolved("machine", n)["printer_variant"])):
            m = bundle.resolved("machine", machine)
            mm = float(m["printer_variant"])
            if {layer([x]) for x in m["nozzle_diameter"]} != {mm}:
                die(f"{machine}: nozzle_diameter {m['nozzle_diameter']} is not its variant {mm}")
            scope["machine"] |= set(m) - META
            rungs = ladder(key, bundle, bundle.compatible("process", machine))
            if not rungs and key == "orca":
                # OrcaSlicer 2.4.2's K1 SE 0.8 machine names a process it does
                # not ship and no process lists it: the nozzle cannot be bound
                # there, so that row x nozzle offers Creality Print alone.
                missing.append(f"{model} {label(mm)}: {machine!r} has no compatible process in "
                               f"{APPS[key]['label']} (offers Creality Print only at this nozzle)")
                continue
            if not rungs:
                die(f"{machine} has no compatible ladder process")
            for p in rungs:
                scope["process"] |= set(bundle.resolved("process", p)) - META
            dp = default_process(key, bundle, machine, mm, rungs, m.get("default_print_profile"), notes)
            fils = sorted(bundle.compatible("filament", machine))
            if not fils:
                die(f"{machine} has no compatible filament")
            for f in fils:
                scope["filament"] |= set(bundle.resolved("filament", f)) - META
            df = default_filament(bundle, machine, fils, m.get("default_filament_profile"), notes)
            plan.append((model, mm, machine, dp, rungs, fils, df))
    overlap = (scope["machine"] & scope["process"]) | (scope["machine"] & scope["filament"])
    if overlap:
        die(f"{key}: keys in two scopes: {sorted(overlap)}")
    return plan, scope, notes, missing


def settle_overlap(key, scope, cfg, notes):
    """A key both process and filament presets carry (Creality Print's processes
    carry `material_flow_dependent_temperature` and `material_flow_temp_graph`)
    goes where the APP puts it: a per-slot list is a filament key, a scalar a
    process key. Read off the app's own config, never guessed."""
    for k in sorted(scope["process"] & scope["filament"]):
        if k not in cfg:  # the app does not know the key (OrcaSlicer and the material_flow_* pair)
            scope["process"].discard(k)
            scope["filament"].discard(k)
            notes.append(f"{k}: in process and filament presets; the app drops it")
            continue
        if isinstance(cfg[k], list):
            scope["process"].discard(k)
            notes.append(f"{k}: in process and filament presets; the app writes a per-slot list, so a filament key")
        else:
            scope["filament"].discard(k)
            notes.append(f"{k}: in process and filament presets; the app writes one value, so a process key")


def build(key, jobs):
    bundle = Bundle(key)
    version = app_version(key)
    plan, scope, notes, missing = plan_app(key, bundle)
    scratch = tempfile.mkdtemp(prefix=f"gen-creality-{key}-")
    try:
        stl = os.path.join(scratch, "box.stl")
        box_stl(stl)
        work = []
        for model, mm, machine, dp, rungs, fils, df in plan:
            for p in rungs:
                work.append(("p", machine, p, df))
            for f in fils:
                if f != df:  # the default filament's config is the default process's run
                    work.append(("f", machine, dp, f))
        work = list(dict.fromkeys(work))
        print(f"gen-creality-maps: {APPS[key]['label']} {version}, {len(plan)} variants, {len(work)} CLI runs", flush=True)
        results = {}
        with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
            futs = {ex.submit(app_config, key, bundle, j[1], j[2], j[3], stl, scratch): j for j in work}
            for n, fut in enumerate(concurrent.futures.as_completed(futs), 1):
                results[futs[fut]] = fut.result()
                if n % 100 == 0:
                    print(f"  {n}/{len(work)}", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    overruled = collections.Counter()

    def pick(cfg, keys, element0=False, where="", preset=None):
        """The app's config for `keys`, each value checked against the preset's
        own resolved value (`preset`, from the bundle) when it has the key: the
        app's FORM where the two agree (its lists, "15%", "96x96/PNG"), the
        PRESET's value in the app's shape where they do not. Measured 2026-11-03:
        both CLIs drop some keys a filament inherits (OrcaSlicer 2.4.2 read 485 of
        876 Creality filaments at a 200 °C default, "CR-PLA @Ender-3 V4-all" among
        them where its preset says 220; Creality Print 7.3 read eSUN ABS+ at 200
        where its chain says 260), while their GUIs build each system preset from
        the whole chain. The lesson: the bundle is the truth for values."""
        out = {}
        for k in sorted(keys):
            if k not in cfg:
                continue
            v = cfg[k]
            if element0:
                if not isinstance(v, list) or len(v) != 1:
                    die(f"{where}: filament key {k!r} is not a one-entry list: {v!r}")
                v = v[0]
            if preset is not None and k in preset:
                b = shaped(v, preset[k], element0)
                if lenient(b) != lenient(v):
                    overruled[k] += 1
                    v = b
            out[k] = v
        return out

    clamps = sorted({(j[1], k) for j, c in results.items() for k in c.get("_clamped", [])})
    for machine, k in clamps:
        notes.append(f"{machine}: the CLI refuses the preset's own {k!r} as out of range; "
                     f"read with it clamped, the preset's value kept in the map")
    settle_overlap(key, scope, results[("p", plan[0][2], plan[0][3], plan[0][6])], notes)
    printers = {}
    entries = {"machine": [], "process": [], "filament": []}
    for model, mm, machine, dp, rungs, fils, df in plan:
        cfg0 = results[("p", machine, dp, df)]
        if cfg0.get("printer_settings_id") != machine:
            die(f"{machine} came back as {cfg0.get('printer_settings_id')!r}")
        mcfg = pick(cfg0, scope["machine"], preset=bundle.resolved("machine", machine))
        # A trap: an app CLI that ignores a loaded machine's `inherits`.
        raw = bundle.resolved("machine", machine)
        for k in ("gcode_flavor", "printable_height"):
            if str(mcfg.get(k)) != str(raw.get(k)):
                die(f"{machine}: {k} came back {mcfg.get(k)!r}, the preset chain says {raw.get(k)!r}")
        if [layer([x]) for x in mcfg["nozzle_diameter"]] != [mm]:
            die(f"{machine}: nozzle_diameter came back {mcfg['nozzle_diameter']!r}")
        bed = bed_of(mcfg["printable_area"], mcfg["printable_height"])
        if bed != bed_of(raw["printable_area"], raw["printable_height"]):
            die(f"{machine}: the bed came back {bed}, the preset says {raw['printable_area']!r}")
        pentries = []
        for p in rungs:
            cfg = results[("p", machine, p, df)]
            if cfg.get("print_settings_id") != p:
                die(f"{p} came back as {cfg.get('print_settings_id')!r}")
            if pick(cfg, scope["machine"], preset=bundle.resolved("machine", machine)) != mcfg:
                die(f"{machine}'s machine values moved with the process {p}")
            pc = pick(cfg, scope["process"], preset=bundle.resolved("process", p))
            pentries.append({
                "process_id": p,
                "layer_mm": layer(pc["layer_height"]),
                "initial_layer_mm": layer(pc["initial_layer_print_height"]),
                "config": pc,
            })
            entries["process"].append(pc)
        fentries = []
        for f in fils:
            cfg = results[("p", machine, dp, df)] if f == df else results[("f", machine, dp, f)]
            if cfg.get("filament_settings_id") != [f]:
                die(f"{f} came back as {cfg.get('filament_settings_id')!r}")
            fc = pick(cfg, scope["filament"], element0=True, where=f, preset=bundle.resolved("filament", f))
            r = bundle.resolved("filament", f)
            fentries.append({
                "name": f.split(" @")[0],
                "preset": f,
                "filament_id": str(r.get("filament_id", "")),
                "material": fc.get("filament_type", ""),
                "config": fc,
            })
            entries["filament"].append(fc)
        entries["machine"].append(mcfg)
        mm_model = bundle.models.get(raw["printer_model"], {})
        row = next(r for r in ROWS if r[0] == model)
        e = printers.setdefault(model, {
            "bed": bed,
            "printer_model": raw["printer_model"],
            "color_unit": row[3],
            "slots": row[4],
            "max_slots": row[5],
            "default_plate": mm_model.get("default_bed_type") or "Textured PEI Plate",
            "nozzles": {},
        })
        if e["bed"] != bed:
            die(f"{model}'s bed differs between nozzles")
        e["nozzles"][label(mm)] = {
            "printer_id": machine,
            "process_id": dp,
            "default_filament": df.split(" @")[0],
            "machine": mcfg,
            "processes": pentries,
            "filaments": fentries,
        }

    if overruled:
        notes.append("the CLI dropped inherited values, the preset's own kept (key: presets): "
                     + ", ".join(f"{k} {n}" for k, n in overruled.most_common(12))
                     + (" ..." if len(overruled) > 12 else ""))
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

    out = {
        "_comment": f"GENERATED by scripts/gen-creality-maps.py from {APPS[key]['label']}'s own configs. Do not hand-edit.",
        "source": {"app": f"{APPS[key]['label']} {version}", "profiles": bundle.index.get("version")},
        "project_version": version,
        "plates": PLATES[key],
        "spools": {
            "creality": {f"{m} {t}": v for (m, t), v in sorted(SPOOLS["creality"].items())},
            "other": {f"{m} {t}": v for (m, t), v in sorted(SPOOLS["other"].items())},
            "material": SPOOLS["material"],
            "alias": MATERIAL_ALIAS,
        },
        "machine_base": bases["machine"],
        "process_base": bases["process"],
        "filament_base": bases["filament"],
        "printers": printers,
    }
    check_traps(key, bundle, out, notes)
    summary = (f"{len(plan)} variants, {sum(len(p[4]) for p in plan)} processes, "
               f"{sum(len(p[5]) for p in plan)} filaments")
    return out, summary, notes, missing


def check_traps(key, bundle, out, notes):
    """The preset traps. Each one stops the generator if it is not handled,
    and if a trap DISAPPEARS from the presets it stops too, so the list is
    re-read by a person rather than silently going stale."""
    P = out["printers"]

    # 1. The K2 SE bed: what the slicers enforce, not the marketed 220 x 220 x 250.
    if P["Creality K2 SE"]["bed"] != [220.0, 215.0, 245.0]:
        die(f"trap 1: the K2 SE bed is {P['Creality K2 SE']['bed']}, expected [220, 215, 245]")

    # 2. Broken default process names: never written, always replaced by a rung.
    for model, e in P.items():
        for nz, v in e["nozzles"].items():
            if not re.match(r"^\d+(?:\.\d+)?mm\s", v["process_id"]):
                die(f"trap 2: {model} {nz} defaults to {v['process_id']!r}, which has no layer height")
            if v["process_id"] not in [p["process_id"] for p in v["processes"]]:
                die(f"trap 2: {model} {nz}'s default process is not on its ladder")
    if key == "creality":
        for machine in ("Creality K2 0.2 nozzle", "Creality K2 Pro 0.2 nozzle", "Creality Hi 0.6 nozzle"):
            named = bundle.resolved("machine", machine).get("default_print_profile", "")
            if not named.startswith(" @"):
                die(f"trap 2: {machine} now names {named!r}; the broken-name trap is gone, re-check it")

    # 3. Irregular names, read verbatim; heights from `layer_height`, never the name.
    if key == "creality":
        for model, nz, name, mm in (("Creality K1 SE", "0.4", "0.20mm  Standard @Creality K1 SE 0.4 nozzle", 0.2),
                                    ("Creality Hi", "0.2", "0.1mm Standard @Creality Hi 0.2 nozzle", 0.1)):
            ps = {p["process_id"]: p["layer_mm"] for p in P[model]["nozzles"][nz]["processes"]}
            if ps.get(name) != mm:
                die(f"trap 3: {model} {nz} lacks {name!r} at {mm} mm (ladder: {ps})")

    # 4. Comma-joined printable_area in the bundle; the bed came from the app's list.
    commas = [n for n in bundle.instantiable("machine")
              if isinstance(bundle.resolved("machine", n).get("printable_area"), str)
              and any(r[1 if key == "creality" else 2] == bundle.resolved("machine", n).get("printer_model") for r in ROWS)]
    if not commas:
        die("trap 4: no row's printable_area is comma-joined any more; re-check the trap")

    # 5. Creality Print's K2 Pro 0.8 names a bare-string default filament.
    if key == "creality":
        named = bundle.resolved("machine", "Creality K2 Pro 0.8 nozzle").get("default_filament_profile")
        if isinstance(named, list):
            die(f"trap 5: the K2 Pro 0.8's default filament is a list again ({named!r}); re-check it")
        if P["Creality K2 Pro"]["nozzles"]["0.8"]["default_filament"] != "Hyper PLA":
            die("trap 5: the K2 Pro 0.8 does not default to Hyper PLA")

    # Decision 7: a Creality PLA Basic spool binds Hyper PLA wherever the app has it.
    for model, e in P.items():
        for nz, v in e["nozzles"].items():
            names = {f["name"] for f in v["filaments"]}
            if "Hyper PLA" in names and bind(out, v, "Creality", "PLA", "Basic") != "Hyper PLA":
                die(f"decision 7: {model} {nz} binds {bind(out, v, 'Creality', 'PLA', 'Basic')!r} for Creality PLA Basic")


def bind(m, variant, brand, material, ftype):
    """The filament NAME a spool binds to on one variant, or None (the export
    then refuses, as the U1 does): the reference for the phase 2 Rust binder,
    and what --report prints. Order: the brand's own list for the material as
    the catalogue names it, then for its alias (PA6 -> PA); the typed Generic;
    then the material's plain names."""
    names = {f["name"] for f in variant["filaments"]}
    sp = m["spools"]
    raw = (material or "PLA").upper()
    mats = list(dict.fromkeys([raw, sp["alias"].get(raw, raw)]))
    table = sp["creality"] if brand.strip().lower() == "creality" else sp["other"]
    tried = []
    for mat in mats:
        tried += table.get(f"{mat} {ftype}", [])
    if ftype and ftype != "Basic":
        tried += [f"Generic {mat}-{ftype}" for mat in mats]
    for mat in mats:
        tried += [t.format(m=mat) for t in sp["material"]]
    for t in tried:
        if t in names:
            return t
    return None


def encode(out):
    blob = json.dumps(out, sort_keys=True, separators=(",", ":")).encode()
    buf = io.BytesIO()
    # mtime=0 so a regeneration from the same profiles is byte-identical.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(blob)
    return blob, buf.getvalue()


def load(key, src=PRESETS):
    return json.loads(gzip.decompress((src / APPS[key]["map"]).read_bytes()))


def report():
    """The row and binding tables, from the COMMITTED maps (no slicer needed)."""
    maps = {k: load(k) for k in APPS if (PRESETS / APPS[k]["map"]).exists()}
    cp = maps["creality"]
    orca = maps.get("orca", {"printers": {}})
    print("| Row | Bed (mm) | Nozzles | Default plate | Quality ladder at 0.4 (default) | Slots / max | OrcaSlicer |")
    print("|---|---|---|---|---|---|---|")
    for model, e in cp["printers"].items():
        nz = sorted(e["nozzles"], key=float)
        v = e["nozzles"].get("0.4")
        lad = ", ".join(f"{p['layer_mm']:g}" for p in v["processes"])
        dflt = next(p["layer_mm"] for p in v["processes"] if p["process_id"] == v["process_id"])
        o = orca["printers"].get(model)
        oc = "none" if o is None else ", ".join(sorted(o["nozzles"], key=float))
        if o is not None and o["bed"] != e["bed"]:
            oc += f" (bed {o['bed']})"
        w, d, h = e["bed"]
        print(f"| {model} | {w:g} x {d:g} x {h:g} | {', '.join(nz)} | {e['default_plate']} | {lad} ({dflt:g}) "
              f"| {e['slots']} / {e['max_slots']} | {oc} |")
    print()
    plate_keys = {"Cool Plate": "cool_plate_temp", "Engineering Plate": "eng_plate_temp",
                  "High Temp Plate": "hot_plate_temp", "Textured PEI Plate": "textured_plate_temp",
                  "Epoxy Resin Plate": "epoxy_resin_plate_temp"}
    for key, m in maps.items():
        print(f"Plates in {m['source']['app']} the 0.4 default PLA heats (menu: {', '.join(m['plates'])}):")
        for model, e in m["printers"].items():
            v = e["nozzles"].get("0.4")
            f = next(f for f in v["filaments"] if f["name"] == v["default_filament"])
            cfg = dict(m["filament_base"])
            cfg.update({k: x for k, x in f["config"].items() if x is not None})
            heated = [pl for pl in m["plates"] if str(cfg.get(plate_keys[pl], "0")) not in ("0", "")]
            print(f"  {model} ({v['default_filament']}, default {e['default_plate']}): {', '.join(heated)}")
        print()
    probes = [("Creality", "PLA", "Basic"), ("Creality", "PLA", "Matte"), ("Creality", "PLA", "Silk"),
              ("Creality", "PLA", "CF"), ("Creality", "PLA", "Wood"), ("Creality", "PETG", "Basic"),
              ("Creality", "PETG", "Translucent"), ("Creality", "ABS", "Basic"), ("Creality", "PC", "Basic"),
              ("Creality", "PPA", "CF"), ("Creality", "TPU", "Basic"),
              ("Other", "PLA", "Basic"), ("Other", "PLA", "Silk"), ("Other", "PETG", "Basic"),
              ("Other", "ASA", "Basic"), ("Other", "PCTG", "Basic"), ("Other", "PA6", "CF"), ("Other", "TPU", "Basic")]
    for key, m in maps.items():
        print(f"Bindings in {m['source']['app']} at 0.4 mm (K2 Plus | K1C | SPARKX i7):")
        for brand, mat, t in probes:
            cells = []
            for model in ("Creality K2 Plus", "Creality K1C", "Creality SPARKX i7"):
                v = m["printers"].get(model, {}).get("nozzles", {}).get("0.4")
                cells.append(bind(m, v, brand, mat, t) or "NONE" if v else "-")
            print(f"  {brand} {mat} {t}: {' | '.join(cells)}")
        print()
    for key, m in maps.items():
        gaps = collections.defaultdict(list)
        mats = ["PLA", "PETG", "ABS", "ASA", "TPU", "PC", "PA6", "PCTG", "PVA", "HIPS", "PET", "PP"]
        for model, e in m["printers"].items():
            for nz, v in e["nozzles"].items():
                for mat in mats:
                    if bind(m, v, "Other", mat, "Basic") is None:
                        gaps[mat].append(f"{model} {nz}")
        print(f"Materials with no preset in {m['source']['app']} (refused at export):")
        for mat, where in gaps.items():
            print(f"  {mat}: {len(where)} variants: {', '.join(where)}")
        print()


def materials(m, v):
    """The materials one variant binds an unbranded, untyped spool of: the
    server's `stl3mf::fork_materials`, which `the_client_creality_tables_match_the_maps`
    compares with these tables."""
    out = set()
    for f in v["filaments"]:
        cfg = dict(m["filament_base"])
        cfg.update({k: x for k, x in f["config"].items() if x is not None})
        t = cfg.get("filament_type")
        if isinstance(t, str) and t and bind(m, v, "", t, "Basic") is not None:
            out.add(t)
    return sorted(out)


def client_blocks():
    """The client mirrors, printed ready to paste into web/user/src/api.ts:
    `CREALITY_QUALITY` and `CREALITY_BEDS` (Creality Print's ladders and beds, which
    the Settings quality line and every fit warning read) and the two refusal
    tables `CREALITY_MATERIALS` / `ORCA_CREALITY_MATERIALS` (per app: the Download
    page refuses a material the bound app has no preset for, before the click).
    Reads the COMMITTED maps, so it needs no slicer."""
    cp, orca = load("creality"), load("orca")
    nz_sorted = lambda e: sorted(e["nozzles"].items(), key=lambda kv: float(kv[0]))
    print("const CREALITY_QUALITY: Record<string, Record<string, { heights: number[]; default: number }>> = {")
    for model, e in cp["printers"].items():
        parts = []
        for nz, v in nz_sorted(e):
            heights = sorted({round(p["layer_mm"], 3) for p in v["processes"]})
            default = next(round(p["layer_mm"], 3) for p in v["processes"] if p["process_id"] == v["process_id"])
            parts.append(f'"{nz}": {{ heights: [{", ".join(f"{h:g}" for h in heights)}], default: {default:g} }}')
        print(f'  "{model}": {{ {", ".join(parts)} }},')
    print("};")
    print("const CREALITY_BEDS: Record<string, Bed> = {")
    for model, e in cp["printers"].items():
        w, d, h = e["bed"]
        print(f'  "{model}": {{ w: {w:g}, d: {d:g}, h: {h:g} }},')
    print("};")
    for name, m in (("CREALITY_MATERIALS", cp), ("ORCA_CREALITY_MATERIALS", orca)):
        print(f"const {name}: Record<string, string[]> = {{")
        for model, e in m["printers"].items():
            for nz, v in nz_sorted(e):
                mats = ", ".join(f'"{x}"' for x in materials(m, v))
                print(f'  "{model} {nz}": [{mats}],')
        print("};")


def main():
    if "--client" in sys.argv:
        client_blocks()
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", choices=list(APPS), action="append")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=str(PRESETS))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
        return 0
    for key in a.app or list(APPS):
        require_readable(key)
    rc = 0
    for key in a.app or list(APPS):
        exe = APPS[key]["dir"] / APPS[key]["bin"]
        if not os.access(exe, os.X_OK):
            die(f"{exe} not found (is {APPS[key]['label']} installed?)")
        out, summary, notes, missing = build(key, a.jobs)
        for n in notes:
            print(f"  {n}")
        for n in missing:
            print(f"  NOT IN THIS APP: {n}")
        blob, gz = encode(out)
        path = Path(a.out) / APPS[key]["map"]
        if a.check:
            if not path.exists() or path.read_bytes() != gz:
                print(f"gen-creality-maps: {path} is STALE against {out['source']['app']} "
                      f"profiles {out['source']['profiles']}: regenerate it", file=sys.stderr)
                rc = 1
            else:
                print(f"{key} map OK: {path.name} matches {out['source']['app']} profiles "
                      f"{out['source']['profiles']} ({summary})")
            continue
        path.write_bytes(gz)
        print(f"wrote {path}: {len(blob):,} bytes JSON, {len(gz):,} gzipped; {summary}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
