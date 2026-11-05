#!/usr/bin/env python3
"""Regenerate `presets/elegoo_map.json.gz` (ElegooSlicer) and
`presets/elegoo_orca_map.json.gz` (OrcaSlicer's Elegoo set) from the two apps
installed on this machine.

**Why two maps.** An Elegoo printer is
offered ElegooSlicer (the maker default) and OrcaSlicer, and each must bind ITS OWN
presets. Measured 2026-11-03 on ElegooSlicer 1.5.3.5 (profiles 01.05.03.05) against
OrcaSlicer 2.4.2 (Elegoo profiles 02.04.00.06), for the Centauri Carbon, Centauri
Carbon 2 and Centauri 2 at 0.2 to 0.8 mm:

  * the PROCESS presets are the same names and the same values (0 differences);
  * the MACHINE presets are the same names with 4 to 7 keys apart (each app's own
    capability keys, `support_mms` against `support_multi_filament`, and on the
    CC2 and C2 `default_bed_type` and the filament load and unload times);
  * the FILAMENT presets are the same names but one: ElegooSlicer's
    `Elegoo PLA Translucent @ECC2` / `@EC2` is OrcaSlicer's `Elegoo PLA
    Translucent2 @...` (ElegooSlicer records the rename in `renamed_from`), and the
    high-temperature presets differ in `cool_plate_temp`.

So one map with an Orca delta would hold almost nothing but would need its own
reader; two maps of the SAME `ForkMap` shape are one more `Fork` variant each and
no new code. Every name a file writes is read from the app that file targets.

**The binding key.** A filament's `name` (what `elegoo_filament` matches) is the
ElegooSlicer name less its "@..." suffix, in BOTH maps; `preset` is the app's own
full name (what the file writes). So "Elegoo PLA Translucent" binds `Elegoo PLA
Translucent2 @ECC2` in the OrcaSlicer map, through ElegooSlicer's `renamed_from`.

**Values from each bundle's profile JSON, resolved here** (the Snapmaker method):
parent (`inherits`, own vendor first, then any vendor) < each `include` < own keys,
presets located through the vendor index (`Elegoo.json`'s lists and `sub_path`),
every scalar a string, a per-flow-variant filament list cut to the "standard"
variant. The project `version` is what each app's own project export writes
(ElegooSlicer "1.5.3.5", OrcaSlicer 2.4.2 "02.06.00.51", not its app version), so
it is read from a CLI `--export-3mf` of a box; that is the only CLI call.

**The quality ladder** (a layer height per rung): every compatible process but
"Strength" (it shares its height with "Standard"); one preset per height.

**Names are READ, never composed**: presets are listed from the vendor index and
kept where `compatible_printers` names the machine.

    python3 scripts/gen-elegoo-maps.py              # write both maps
    python3 scripts/gen-elegoo-maps.py --check      # exit 1 if either is stale
    python3 scripts/gen-elegoo-maps.py --client     # the api.ts mirrors

Run it when either app ships new Elegoo profiles, or an Elegoo row is added to
`stl3mf::PRINTERS` (the models are READ from that table). `--check` needs both
apps; the cargo tests `every_elegoo_row_binds_a_complete_config_in_both_apps` and
`the_client_elegoo_tables_match_the_map` are the gate's half.
"""
import collections
import gzip
import io
import json
import re
import subprocess
import sys
import tempfile
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
    "elegoo": {"label": "ElegooSlicer",
               "app": _first_app("/Applications/ElegooSlicer.app", Path.home() / "Applications" / "ElegooSlicer.app"),
               "bin": "Contents/MacOS/ElegooSlicer", "map": "elegoo_map.json.gz"},
    "orca": {"label": "OrcaSlicer", "app": Path("/Applications/OrcaSlicer.app"),
             "bin": "Contents/MacOS/OrcaSlicer", "map": "elegoo_orca_map.json.gz"},
}
VENDOR = "Elegoo"

META = {
    "name", "inherits", "include", "from", "instantiation", "setting_id", "type", "version",
    "compatible_printers", "compatible_printers_condition", "compatible_prints",
    "compatible_prints_condition", "renamed_from", "description", "filament_id",
    "print_settings_id", "printer_settings_id", "filament_settings_id",
    "default_print_profile", "default_filament_profile", "print_compatible_printers",
    "is_custom_defined",
}
VARIANT = "standard"
_LISTS = {"machine": ("machine_list",), "process": ("process_list",), "filament": ("filament_list",)}


def die(msg):
    sys.exit(f"gen-elegoo-maps: {msg}")


def catalogue():
    """The Elegoo rows of `stl3mf::PRINTERS`, in table order."""
    if not (SERVER / "stl3mf.rs").exists():  # the public presets repo: the map's own list
        return list(json.loads(gzip.decompress((PRESETS / APPS["elegoo"]["map"]).read_bytes()))["printers"])
    text = (SERVER / "stl3mf.rs").read_text()
    rows = re.findall(
        r'Printer\s*\{\s*model:\s*"([^"]+)"\s*,\s*maker:\s*MAKER_ELEGOO\s*,\s*'
        r'presets:\s*Presets::Elegoo\s*\{', text)
    if not rows:
        die("parsed ZERO Elegoo rows from stl3mf.rs, the format moved")
    return rows


def label(mm):
    s = f"{mm:.2f}".rstrip("0")
    return s[:-1] if s.endswith(".") else s


class Bundle:
    """Every JSON vendor of one app, presets located through the vendor indexes."""

    def __init__(self, profiles):
        self.dir = profiles
        self.index = {}  # (kind, name) -> [(vendor, path)]
        for vf in sorted(profiles.glob("*.json")):
            try:
                data = json.loads(vf.read_text(encoding="utf-8-sig"))
            except ValueError:
                continue
            for kind, lists in _LISTS.items():
                for lst in lists:
                    for e in data.get(lst) or []:
                        if e.get("name") and e.get("sub_path"):
                            self.index.setdefault((kind, e["name"]), []).append((vf.stem, profiles / vf.stem / e["sub_path"]))
        self._raw = {}
        self._res = {}

    def raw(self, kind, name, vendor=None):
        hits = self.index.get((kind, name)) or []
        hit = next((h for h in hits if h[0] == vendor), hits[0] if hits else None)
        if hit is None:
            return None, None
        if hit[1] not in self._raw:
            self._raw[hit[1]] = json.loads(hit[1].read_text(encoding="utf-8-sig"))
        return hit[0], self._raw[hit[1]]

    def resolved(self, kind, name, vendor=VENDOR):
        key = (kind, name, vendor)
        if key not in self._res:
            v, d = self.raw(kind, name, vendor)
            if d is None:
                return None
            out = {}
            if d.get("inherits"):
                parent = self.resolved(kind, d["inherits"], v)
                if parent is None:
                    die(f"{kind} {name!r} inherits missing {d['inherits']!r}")
                out.update(parent)
            inc = d.get("include") or []
            for t in [inc] if isinstance(inc, str) else inc:
                tpl = self.resolved(kind, t, v)
                if tpl is None:
                    die(f"{kind} {name!r} includes missing {t!r}")
                out.update({k: x for k, x in tpl.items() if k not in ("name", "inherits", "instantiation")})
            out.update(d)
            self._res[key] = out
        return self._res[key]

    def vendor_names(self, kind):
        return [n for (k, n), hits in self.index.items() if k == kind and any(h[0] == VENDOR for h in hits)]

    def instantiable(self, kind):
        return [n for n in self.vendor_names(kind)
                if str(self.resolved(kind, n).get("instantiation", "true")).lower() == "true"]

    def compatible(self, kind, machine):
        return sorted(n for n in self.instantiable(kind)
                      if machine in (self.resolved(kind, n).get("compatible_printers") or []))


def stringify(v):
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
    flow = resolved.get("filament_flow_support") or [VARIANT]
    i = flow.index(VARIANT) if VARIANT in flow else 0
    out = {}
    for k in sorted(resolved):
        if k in META:
            continue
        v = stringify(resolved[k])
        if isinstance(v, list):
            if not v:
                out[k] = ""
                continue
            v = v[i] if i < len(v) else v[0]
        out[k] = v
    return out


def ladder(bundle, procs):
    def h(p):
        return round(layer(bundle.resolved("process", p)["layer_height"]), 3)
    out = sorted((p for p in procs if " Strength @" not in p), key=h)
    if len({h(p) for p in out}) != len(out):
        die(f"two ladder presets share a height: {out}")
    return out


def project_version(key, bundle, machine, process, filament):
    """The `version` the app's own project export writes (a CLI --export-3mf of a
    10 mm box with these presets)."""
    app = APPS[key]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        files = []
        for kind, name in (("machine", machine), ("process", process), ("filament", filament)):
            body = {"type": kind, "name": name, "from": "system", "instantiation": "true", "inherits": ""}
            body.update({k: v for k, v in bundle.resolved(kind, name).items() if k not in ("inherits", "include", "name")})
            p = t / f"{kind}.json"
            p.write_text(json.dumps(body))
            files.append(str(p))
        stl = t / "box.stl"
        stl.write_text("solid b\n" + "".join(
            f"facet normal 0 0 0\nouter loop\n{''.join(f'vertex {x} {y} {z}' + chr(10) for x, y, z in tri)}endloop\nendfacet\n"
            for tri in (((0, 0, 0), (10, 0, 0), (0, 10, 0)), ((0, 0, 0), (0, 10, 0), (0, 0, 10)),
                        ((0, 0, 0), (0, 0, 10), (10, 0, 0)), ((10, 0, 0), (0, 0, 10), (0, 10, 0)))) + "endsolid b\n")
        out = t / "p.3mf"
        r = subprocess.run([str(app["app"] / app["bin"]), "--datadir", str(t / "dd"), "--load-settings",
                            f"{files[0]};{files[1]}", "--load-filaments", files[2], "--export-3mf", str(out),
                            str(stl)], cwd=t, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not out.exists():
            die(f"{app['label']} CLI project export failed (rc {r.returncode})")
        import zipfile
        with zipfile.ZipFile(out) as z:
            return json.loads(z.read("Metadata/project_settings.config"))["version"]


def require_readable(key):
    """Refuse a vendor format this cannot read before anything runs or is
    written. OrcaSlicer 2.5 packs each vendor into a .opc with no JSON beside it."""
    profiles = APPS[key]["app"] / "Contents" / "Resources" / "profiles"
    if (profiles / f"{VENDOR}.opc").exists() and not (profiles / f"{VENDOR}.json").exists():
        die(f"{APPS[key]['label']} keeps its {VENDOR} presets in a packed {VENDOR}.opc "
            "(OrcaSlicer 2.5 onward), which this generator cannot read. Filafy's OrcaSlicer "
            "data stays pinned to 2.4.2: run this with OrcaSlicer 2.4.2 installed. "
            "Nothing was written.")


def build(key, models, renames):
    app = APPS[key]
    profiles = app["app"] / "Contents" / "Resources" / "profiles"
    if not (profiles / VENDOR).is_dir():
        die(f"{profiles / VENDOR} not found (is {app['label']} installed?)")
    bundle = Bundle(profiles)
    for kind in _LISTS:
        if not bundle.vendor_names(kind):
            die(f"no {VENDOR} {kind} presets in {profiles}: an unknown bundle format?")
    index = json.loads((profiles / f"{VENDOR}.json").read_text(encoding="utf-8-sig"))

    scope = {"machine": set(), "process": set(), "filament": set()}
    plan = []
    for model in models:
        machines = [n for n in bundle.instantiable("machine")
                    if bundle.resolved("machine", n).get("printer_model") == model]
        if not machines:
            die(f"{app['label']}: no machine preset has printer_model {model!r}")
        for machine in sorted(machines, key=lambda n: float(bundle.resolved("machine", n)["printer_variant"])):
            m = bundle.resolved("machine", machine)
            mm = float(m["printer_variant"])
            if {layer([x]) for x in m["nozzle_diameter"]} != {mm}:
                die(f"{machine}: nozzle_diameter {m['nozzle_diameter']} is not its variant {mm}")
            scope["machine"] |= set(m) - META
            rungs = ladder(bundle, bundle.compatible("process", machine))
            if not rungs:
                die(f"{machine} has no compatible process")
            for p in rungs:
                scope["process"] |= set(bundle.resolved("process", p)) - META
            dp = m.get("default_print_profile")
            if dp not in rungs:
                die(f"{machine}: default process {dp!r} is not on its ladder {rungs}")
            fils = bundle.compatible("filament", machine)
            if not fils:
                die(f"{machine} has no compatible filament")
            for f in fils:
                scope["filament"] |= set(bundle.resolved("filament", f)) - META
            plan.append((model, mm, machine, dp, rungs, fils))

    overlap = (scope["machine"] & scope["process"]) | (scope["machine"] & scope["filament"]) | (scope["process"] & scope["filament"])
    if overlap:
        die(f"keys in two scopes: {sorted(overlap)}")

    printers = {}
    entries = {"machine": [], "process": [], "filament": []}
    for model, mm, machine, dp, rungs, fils in plan:
        mcfg = config(bundle.resolved("machine", machine), scope["machine"])
        pentries = []
        for p in rungs:
            pc = config(bundle.resolved("process", p), scope["process"])
            pentries.append({"process_id": p, "layer_mm": layer(pc["layer_height"]),
                             "initial_layer_mm": layer(pc["initial_layer_print_height"]), "config": pc})
            entries["process"].append(pc)
        fentries = []
        for f in fils:
            r = bundle.resolved("filament", f)
            fc = filament_values(r)
            fentries.append({
                "name": renames.get(f, f).split(" @")[0],
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
        ex = mcfg.get("bed_exclude_area") or []
        exclude = None
        if ex and ex != [""]:
            exs = [float(p.split("x")[0]) for p in ex]
            eys = [float(p.split("x")[1]) for p in ex]
            exclude = [min(exs), min(eys), max(exs), max(eys)]
        printers.setdefault(model, {"bed": bed, "exclude": exclude, "nozzles": {}})
        if printers[model]["bed"] != bed or printers[model]["exclude"] != exclude:
            die(f"{model}'s bed or purge corner differs between nozzles")
        printers[model]["nozzles"][label(mm)] = {
            "printer_id": machine, "process_id": dp, "machine": mcfg,
            "processes": pentries, "filaments": fentries,
        }

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

    first = plan[0]
    version = project_version(key, bundle, first[2], first[3], first[5][0])
    plist = (app["app"] / "Contents" / "Info.plist").read_text(errors="replace")
    appv = re.search(r"<key>CFBundleShortVersionString</key>\s*<string>([^<]*)</string>", plist)
    summary = (f"{len(plan)} variants, {sum(len(p[4]) for p in plan)} processes, "
               f"{sum(len(p[5]) for p in plan)} filaments")
    return {
        "_comment": f"GENERATED by scripts/gen-elegoo-maps.py from {app['label']}'s bundled Elegoo profiles. Do not hand-edit.",
        "source": {"app": f"{app['label']} {appv.group(1) if appv else '?'}", "profiles": index.get("version")},
        "project_version": version,
        "machine_base": bases["machine"],
        "process_base": bases["process"],
        "filament_base": bases["filament"],
        "printers": printers,
    }, summary


def renames_from_elegoo():
    """ElegooSlicer's own renames (`renamed_from`), old name -> its name now: how an
    OrcaSlicer preset gets ElegooSlicer's binding key."""
    profiles = APPS["elegoo"]["app"] / "Contents" / "Resources" / "profiles"
    b = Bundle(profiles)
    out = {}
    for n in b.vendor_names("filament"):
        _, d = b.raw("filament", n, VENDOR)
        for old in str(d.get("renamed_from") or "").split(";"):
            if old.strip():
                out[old.strip()] = n
    return out


def encode(out):
    blob = json.dumps(out, sort_keys=True, separators=(",", ":")).encode()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(blob)
    return blob, buf.getvalue()


def client_blocks():
    """`ELEGOO_QUALITY` and `ELEGOO_BEDS` for web/user/src/api.ts, read from the
    COMMITTED ElegooSlicer map (the quality ladder is per printer, not per slicer:
    both apps carry the same processes). `the_client_elegoo_tables_match_the_map`
    fails when they drift."""
    m = json.loads(gzip.decompress((PRESETS / APPS["elegoo"]["map"]).read_bytes()))
    print("const ELEGOO_QUALITY: Record<string, Record<string, { heights: number[]; default: number }>> = {")
    for model, e in m["printers"].items():
        parts = []
        for nz, v in sorted(e["nozzles"].items(), key=lambda kv: float(kv[0])):
            heights = sorted({round(p["layer_mm"], 3) for p in v["processes"]})
            default = next(round(p["layer_mm"], 3) for p in v["processes"] if p["process_id"] == v["process_id"])
            parts.append(f'"{nz}": {{ heights: [{", ".join(f"{h:g}" for h in heights)}], default: {default:g} }}')
        print(f'  "{model}": {{ {", ".join(parts)} }},')
    print("};")
    print("const ELEGOO_BEDS: Record<string, Bed> = {")
    for model, e in m["printers"].items():
        w, d, h = e["bed"]
        ex = e.get("exclude")
        extra = f", exclude: [{', '.join(f'{x:g}' for x in ex)}]" if ex else ""
        print(f'  "{model}": {{ w: {w:g}, d: {d:g}, h: {h:g}{extra} }},')
    print("};")


def main():
    args = sys.argv[1:]
    if "--client" in args:
        client_blocks()
        return 0
    for key in APPS:
        require_readable(key)
    models = catalogue()
    renames = renames_from_elegoo()
    stale = 0
    for key in ("elegoo", "orca"):
        out, summary = build(key, models, renames if key == "orca" else {})
        blob, gz = encode(out)
        path = PRESETS / APPS[key]["map"]
        if "--check" in args:
            if (path.read_bytes() if path.exists() else b"") != gz:
                print(f"gen-elegoo-maps: {path} is STALE against {out['source']['app']} "
                      f"profiles {out['source']['profiles']}: regenerate it", file=sys.stderr)
                stale = 1
            else:
                print(f"elegoo map OK: {path.name} matches {out['source']['app']} profiles "
                      f"{out['source']['profiles']} ({summary})")
            continue
        path.write_bytes(gz)
        print(f"wrote {path}: {len(blob):,} bytes JSON, {len(gz):,} gzipped; {summary}; "
              f"project version {out['project_version']}")
    return stale


if __name__ == "__main__":
    sys.exit(main())
