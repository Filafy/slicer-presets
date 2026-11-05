#!/usr/bin/env python3
"""Regenerate presets/bambu_printer_map.json and bambu_filament_map.json
from BambuStudio's official profile repo.

These maps are what make Filafy's 3MF exports open in Bambu Studio with the
printer / process / filament slots bound to the user's REAL system presets
(the binding itself is in Filafy's export code).
Preset names must match Bambu's byte-for-byte, so they are never hand-written:
run this script whenever

  * Bambu releases a new printer (add it to PRINTERS below AND to
    stl3mf::BAMBU_PRINTERS + web/user/src/api.ts BAMBU_PRINTERS),
  * Bambu adds a filament line, or
  * the Filafy library gains a new Bambu material/type pair (add to
    EXTRA_PAIRS if it isn't in seed_filaments.json).

Requires network access to raw.githubusercontent.com. Usage:
    python3 scripts/gen-bambu-maps.py

Every map row's `machine` table names the preset a machine x nozzle lists
where the name the map writes does not, and bambu_preset_compat.json keeps what
that was read from, for the cargo guard. To refresh only those two, online or from
an installed app, moving nothing else; and to test them against the profiles:
    python3 scripts/gen-bambu-maps.py --machine-presets \\
        "/Applications/BambuStudio.app/Contents/Resources/profiles"
    python3 scripts/gen-bambu-maps.py --check [DIR]

A full run on Bambu Studio 02.09.01.70's profiles moves existing rows (the
H2D's 0.4 default process comes back empty, the H2C's 0.6 and 0.8 lists gain
presets), so the H3C rows alone were added. Read that diff before committing one.

Every filament map entry also carries the `density` of the preset it
names (resolved through `inherits`), which the "Other" export writes for
slicers that do not have that preset. To fill or refresh ONLY that field from
an installed Bambu Studio, offline, leaving every other byte of the map alone:
    python3 scripts/gen-bambu-maps.py --densities \
        "/Applications/BambuStudio.app/Contents/Resources/profiles/BBL"

presets/bambu_orca_missing.json lists the Bambu filament presets
Filafy can name that OrcaSlicer's latest RELEASE does not bundle (on printers
that release supports), with Bambu Studio's own temperatures for them and for
every preset that can share a file with them. The export writes those
temperatures for such a file, because Orca builds a slot whose preset it lacks
from its default filament (200 °C nozzle, 45 °C bed). The full run refreshes it;
to refresh ONLY it (after an OrcaSlicer release), optionally pinned to a tag:
    python3 scripts/gen-bambu-maps.py --orca-missing [v2.4.2]
When that release bundles the presets, the list comes back empty and the
exports return to the bytes they had before this list.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "https://raw.githubusercontent.com/bambulab/BambuStudio/master/resources/profiles/"
# OrcaSlicer's profiles, read at a RELEASE tag (never `main`: members run
# releases), and the API that names the latest one.
ORCA_RAW = "https://raw.githubusercontent.com/OrcaSlicer/OrcaSlicer/{tag}/resources/profiles/"
ORCA_LATEST = "https://api.github.com/repos/OrcaSlicer/OrcaSlicer/releases/latest"
ROOT = Path(__file__).resolve().parent.parent
PRESETS = ROOT / "presets"
SERVER = ROOT / "server" / "src"  # Filafy's own source: not in the public presets repo

# Every model offered in Settings → Printer. Must stay in sync with
# stl3mf::BAMBU_PRINTERS and web/user/src/api.ts BAMBU_PRINTERS.
PRINTERS = [
    "Bambu Lab X1 Carbon",
    "Bambu Lab X1",
    "Bambu Lab X1E",
    "Bambu Lab P1S",
    "Bambu Lab P1P",
    "Bambu Lab A1",
    "Bambu Lab A1 mini",
    "Bambu Lab H2D",
    "Bambu Lab H2D Pro",
    "Bambu Lab H2S",
    "Bambu Lab H2C",
    "Bambu Lab P2S",
    "Bambu Lab X2D",
    "Bambu Lab A2L",
    "Bambu Lab H3C",
]
NOZZLES = ["0.2", "0.4", "0.6", "0.8"]

# Filafy library (material, type) pairs not present in seed_filaments.json.
# ("PETG", "Matte") is Bambu PETG Matte (GFG03, launched 2026-09-28),
# which the OFD seed does not list yet.
EXTRA_PAIRS = [("PLA", "Basic"), ("PETG", "Matte")]

# The @BBL printer codes used in preset names (for filament variant checks).
CODES = ["X1C", "X1", "X1E", "P1S", "P1P", "A1", "A1M", "H2D", "H2DP", "H2S", "H2C", "P2S", "X2D", "A2L", "H3C"]


# Memoized fetches + a polite throttle: the #22b initial-layer resolution walks
# each kept process preset's `inherits` chain, and GitHub's raw host rate-limits
# bursts (observed HTTP 429 after ~10 rapid fetches). Parents like
# fdm_process_common are shared by dozens of presets, so the cache does most of
# the work; the throttle + 429 retry cover the rest.
_FETCH_CACHE = {}
_LAST_FETCH = [0.0]


def fetch_json(path):
    if path in _FETCH_CACHE:
        return _FETCH_CACHE[path]
    url = REPO + urllib.parse.quote(path)
    for attempt in range(4):
        wait = _LAST_FETCH[0] + 0.6 - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST_FETCH[0] = time.time()
        try:
            with urllib.request.urlopen(url) as r:
                data = json.loads(r.read())
            _FETCH_CACHE[path] = data
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                print(f"  rate-limited fetching {path} — waiting 25s", file=sys.stderr)
                time.sleep(25)
                continue
            raise


def as_float(v):
    """Bambu profile values are strings (occasionally single-element lists)."""
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve_process_field(proc_paths, name, key):
    """Walk a process preset's `inherits` chain until `key` appears. Leaf
    presets often omit initial_layer_print_height — it lives in a shared parent
    (fdm_process_common etc.), sometimes several hops up."""
    seen = set()
    cur = name
    while cur and cur not in seen:
        seen.add(cur)
        path = proc_paths.get(cur, f"BBL/process/{cur}.json")
        try:
            j = fetch_json(path)
        except Exception:
            return None
        if key in j:
            return j[key]
        cur = j.get("inherits", "")
    return None


def process_presets(proc_paths, default_process):
    """All process presets available for a machine×nozzle (print quality).

    The machine's default_print_profile tells us which preset FAMILY + nozzle
    suffix this nozzle uses (e.g. "@BBL X1C" for X1/X1C 0.4, "@BBL X1 0.6 nozzle"
    for X1 0.6). Every process whose name ends with that same "@BBL …" tail is a
    quality option for this nozzle; the layer height + tier come straight from
    the name ("0.12mm Fine @BBL X1C" → 0.12mm, "Fine"). Sorted finest-first.

    #22b also records each preset's first-layer height (initial_layer_mm) —
    resolved through the preset's `inherits` chain, because leaf JSONs usually
    omit it and it routinely DIFFERS from layer_mm (a 0.08mm Fine profile still
    prints a 0.2mm first layer). The layered-plaque band math depends on it."""
    if "@BBL" not in default_process:
        return []
    tail = default_process[default_process.index("@BBL"):]
    procs = []
    for name in proc_paths:
        if not name.endswith(tail):
            continue
        m = re.match(r"^(\d+\.\d+)mm\s+(.*?)\s+@BBL", name)
        if not m:
            continue
        procs.append({"process_id": name, "layer_mm": float(m.group(1)), "label": m.group(2).strip()})
    # The machine default must always be selectable.
    if default_process and not any(p["process_id"] == default_process for p in procs):
        m = re.match(r"^(\d+\.\d+)mm\s+(.*?)\s+@BBL", default_process)
        if m:
            procs.append({"process_id": default_process, "layer_mm": float(m.group(1)), "label": m.group(2).strip()})
    procs.sort(key=lambda p: p["layer_mm"])
    for p in procs:
        initial = as_float(resolve_process_field(proc_paths, p["process_id"], "initial_layer_print_height"))
        if initial is None:
            print(f"  WARNING: no initial_layer_print_height for {p['process_id']} — assuming 0.2", file=sys.stderr)
            initial = 0.2
        p["initial_layer_mm"] = initial
    return procs


def nozzle_line_width(proc_paths, procs, default_process):
    """The `line_width` this nozzle's process presets resolve to (mm).

    Filafy overrides `initial_layer_line_width` to exactly this value, so that
    layer 1 draws at the same width as every layer above it and a colour region
    too thin for Bambu's WIDER first-layer line stops disappearing from the
    bottom face. The value therefore has to be Bambu's own, not a formula.

    Measured 2026-09-03 over all 231 process presets this map names: `line_width`
    is a pure function of the NOZZLE (0.2 → 0.22, 0.4 → 0.42, 0.6 → 0.62,
    0.8 → 0.82), which is why one value per nozzle is stored rather than one per
    preset. It is still RESOLVED per preset here, so a future Bambu profile that
    breaks that would be caught rather than silently averaged: a disagreement
    warns loudly and keeps the DEFAULT preset's value.

    (The same sweep found `initial_layer_line_width` already equal to
    `line_width` on the 0.6 and 0.8 nozzles — 88 of the 231 — so the override is
    a no-op there and only the 0.2 and 0.4 nozzles actually change.)"""
    per_preset = {}
    for p in procs:
        w = as_float(resolve_process_field(proc_paths, p["process_id"], "line_width"))
        if w is not None:
            per_preset[p["process_id"]] = round(w, 3)
    if not per_preset:
        # Nothing resolved: emit no value. The exporter then emits no override at
        # all, which leaves Bambu's own first-layer width in place — the rule
        # that an unknown machine fact takes its unknown path deliberately.
        print(f"  WARNING: no line_width resolved for {default_process or '(no default)'}", file=sys.stderr)
        return None
    distinct = sorted(set(per_preset.values()))
    if len(distinct) > 1:
        print(f"  WARNING: line_width differs within one nozzle {distinct} — keeping {default_process}'s", file=sys.stderr)
    return per_preset.get(default_process, distinct[0])


def gen_printer_map():
    """machine/<name>.json per model×nozzle → printer_id (= preset name),
    process_id (= the machine's default_print_profile — this is how non-0.4
    nozzles get their '@BBL <code> <nozzle> nozzle' process names right),
    dual-nozzle (nozzle_diameter arity), the @BBL code, and the full list
    of quality `processes` (each preset's id + layer height + tier)."""
    # name → repo path for every process preset (also feeds the inherits walk).
    proc_paths = {e["name"]: "BBL/" + e["sub_path"] for e in fetch_json("BBL.json").get("process_list", [])}
    out = []
    for model in PRINTERS:
        entry = {"model": model, "nozzles": []}
        for nz in NOZZLES:
            try:
                j = fetch_json(f"BBL/machine/{model} {nz} nozzle.json")
            except Exception:
                continue  # this model doesn't offer this nozzle
            process = j.get("default_print_profile", "")
            m = re.search(r"@BBL ([A-Z0-9]+)", process)
            code = m.group(1) if m else ""
            procs = process_presets(proc_paths, process)
            entry["nozzles"].append(
                {
                    "nozzle": nz,
                    "printer_id": j.get("name", f"{model} {nz} nozzle"),
                    "process_id": process,
                    "dual": len(j.get("nozzle_diameter", ["0.4"])) > 1,
                    # What layer 1 is forced to, so it matches every layer above.
                    "line_width_mm": nozzle_line_width(proc_paths, procs, process),
                    "processes": procs,
                }
            )
            entry["code"] = code or entry.get("code", "")
        if not entry["nozzles"]:
            print(f"  WARNING: no machine profiles found for {model}", file=sys.stderr)
            continue
        out.append(entry)
        noz = ",".join(f"{n['nozzle']}({len(n['processes'])}q)" for n in entry["nozzles"])
        dual = entry["nozzles"][0]["dual"]
        print(f"  {model:22s} code={entry['code']:5s} dual={dual!s:5s} nozzles=[{noz}]")
    return out


def preset_density(load, base):
    """The `filament_density` of the preset a map entry names, walking
    `inherits`. `load(name)` returns a filament preset's JSON or None. The X1C
    variant first (the "Other" reference machine), then the shared base; "" when
    no preset in the chain sets it."""
    for variant in [f"{base} @BBL X1C", f"{base} @base", base]:
        seen, name = set(), variant
        while name and name not in seen:
            seen.add(name)
            j = load(name)
            if j is None:
                break
            if "filament_density" in j:
                v = j["filament_density"]
                return str(v[0] if isinstance(v, list) else v)
            name = j.get("inherits", "")
    return ""


def remote_filament(name):
    try:
        return fetch_json(f"BBL/filament/{name}.json")
    except Exception:
        return None


def fill_densities(profiles_dir):
    """Write `density` into the committed filament map from a LOCAL Bambu
    Studio profile folder, changing nothing else."""
    idx = {}
    for p in Path(profiles_dir, "filament").rglob("*.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(j, dict) and "name" in j:
            idx[j["name"]] = j
    path = PRESETS / "bambu_filament_map.json"
    fmap = json.loads(path.read_text())
    for e in fmap["bambu"] + fmap["generic"]:
        e["density"] = preset_density(idx.get, e["name"])
        print(f"  {e['name']:26s} density {e['density'] or '(none)'}")
    path.write_text(json.dumps(fmap, indent=1) + "\n")


def compat_index(filament_list, printer_map, bases):
    """{preset: [machine presets it lists]} for every preset of `bases`, kept to the
    machines in `printer_map`. `bambu_preset_compat.json` stores it."""
    ids = {nz["printer_id"] for row in printer_map for nz in row["nozzles"]}
    out = {}
    for e in filament_list:
        n = e["name"]
        if n in bases or any(n.startswith(b + " @") for b in bases):
            listed = fetch_json("BBL/" + e["sub_path"]).get("compatible_printers", [])
            out[n] = sorted(set(listed) & ids)
    return dict(sorted(out.items()))


def machine_rows(base, plain, compat, printer_map):
    """{machine preset: filament preset} for one map row: every machine x
    nozzle that the name the map writes does not list, the one "<base> @..." preset
    that does. `plain(row)` is that written name on a printer row, or None when the
    map writes something else there. Bambu Studio replaces a preset the machine
    does not list, and slot 1 lands on the machine's default filament. A machine
    with no such preset is left out. Read from `compatible_printers`, because the
    names follow no rule: "Generic PLA @BBL H3C" serves the H3C's 0.4 to 0.8, the
    X1 uses "Bambu PETG HF @BBL X1C", and the X2D's PLA Basic is only
    "Bambu PLA Basic @BBL X2D 0.4 nozzle"."""
    out = {}
    for row in printer_map:
        written = plain(row)
        for nz in row["nozzles"]:
            pid = nz["printer_id"]
            if written and pid in compat.get(written, []):
                continue
            own = [n for n, printers in compat.items() if n.startswith(base + " @") and pid in printers]
            if len(own) > 1:
                sys.exit(f"  ERROR: more than one {base} preset lists {pid}: {own}")
            if own:
                out[pid] = own[0]
    return out


def add_machine_rows(fmap, printer_map, filament_list):
    """Write every row's `machine` table and return the compat index they came from."""
    bases = [e["name"] for e in fmap["bambu"]] + [e["name"] for e in fmap["generic"]]
    compat = compat_index(filament_list, printer_map, bases)
    for e in fmap["bambu"]:
        codes = e["codes"]
        written = lambda row, e=e, codes=codes: f"{e['name']} @BBL {row['code']}" if row["code"] in codes else None
        e.pop("machine", None)
        rows = machine_rows(e["name"], written, compat, printer_map)
        if rows:
            e["machine"] = rows
    for e in fmap["generic"]:
        e.pop("machine", None)
        rows = machine_rows(e["name"], lambda row, e=e: e["name"], compat, printer_map)
        if rows:
            e["machine"] = rows
    return compat


def use_local_profiles(argv, flag):
    """`flag [DIR]`: read the Bambu profiles from DIR (a `resources/profiles` folder,
    such as an installed app's) instead of the network."""
    i = argv.index(flag)
    if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
        base = Path(argv[i + 1])
        globals()["fetch_json"] = lambda p: json.loads((base / p).read_text(encoding="utf-8-sig"))


def filament_list_and_maps():
    pmap = json.loads((PRESETS / "bambu_printer_map.json").read_text())
    fmap = json.loads((PRESETS / "bambu_filament_map.json").read_text())
    return fetch_json("BBL.json"), pmap, fmap


def write_machine_presets():
    """Only the filament map's `machine` tables and `bambu_preset_compat.json`,
    so a run does not move the printer map's rows or a row's `codes`."""
    idx, pmap, fmap = filament_list_and_maps()
    compat = add_machine_rows(fmap, pmap, idx.get("filament_list", []))
    (PRESETS / "bambu_filament_map.json").write_text(json.dumps(fmap, indent=1) + "\n")
    (PRESETS / "bambu_preset_compat.json").write_text(
        json.dumps({"profiles": idx.get("version", ""), "presets": compat}, indent=0) + "\n")
    n = sum(len(e.get("machine", {})) for e in fmap["bambu"] + fmap["generic"])
    print(f"  {n} machine rows from BBL {idx.get('version', '')}")


def check_machine_presets():
    """Exit 1 when the committed tables are not what these profiles give."""
    idx, pmap, fmap = filament_list_and_maps()
    want = json.loads(json.dumps(fmap))
    compat = add_machine_rows(want, pmap, idx.get("filament_list", []))
    stored = json.loads((PRESETS / "bambu_preset_compat.json").read_text())["presets"]
    if want != fmap or compat != stored:
        sys.exit(f"gen-bambu-maps --check: the machine tables are stale against BBL {idx.get('version', '')}; "
                 "run --machine-presets")
    print(f"gen-bambu-maps --check: machine tables match BBL {idx.get('version', '')}")


def gen_filament_map():
    """(material, type) pairs from the seed library → exact Bambu preset base
    name + GFxxx filament_id + which @BBL codes have a variant; plus the
    Generic per-material fallbacks (with their ids)."""
    if (SERVER / "seed_filaments.json").exists():
        seed = json.loads((SERVER / "seed_filaments.json").read_text())
        fil = seed["filaments"] if isinstance(seed, dict) and "filaments" in seed else seed
        pairs = {(f["material"], f["type"]) for f in fil if f.get("brand") == "Bambu Lab"}
    else:  # the public presets repo has no Filafy library: keep the pairs the map has
        pairs = {(e["material"], e["type"]) for e in json.loads((PRESETS / "bambu_filament_map.json").read_text())["bambu"]}
    pairs = sorted(pairs | set(EXTRA_PAIRS))

    idx = fetch_json("BBL.json")
    names = [e["name"] for e in idx.get("filament_list", [])]
    bases = {}
    for n in names:
        m = re.match(r"^(.*?) @BBL ([A-Z0-9]+)$", n)
        if m and m.group(2) in CODES:
            bases.setdefault(m.group(1), set()).add(m.group(2))

    def candidates(mat, typ):
        c = [f"Bambu {mat} {typ}", f"Bambu {mat}-{typ}"]
        if typ == "Basic":
            c.append(f"Bambu {mat}")
            if mat == "TPU":
                c = ["Bambu TPU 95A", "Bambu TPU for AMS"] + c
        return c

    def filament_id(base):
        for variant in [f"{base} @base", f"{base} @BBL X1C", f"{base} @BBL H2C", base]:
            try:
                fid = fetch_json(f"BBL/filament/{variant}.json").get("filament_id")
                if fid:
                    return fid
            except Exception:
                continue
        return ""

    bambu = []
    for mat, typ in pairs:
        hit = next((c for c in candidates(mat, typ) if c in bases), None)
        if not hit:
            print(f"  WARNING: no Bambu preset found for {mat}/{typ} — will fall back to Generic", file=sys.stderr)
            continue
        bambu.append(
            {"material": mat, "type": typ, "name": hit, "filament_id": filament_id(hit), "codes": sorted(bases[hit]),
             "density": preset_density(remote_filament, hit)}
        )
        print(f"  {mat}/{typ:12s} -> {hit:26s} id={bambu[-1]['filament_id']} codes={len(bambu[-1]['codes'])}")

    plain = {n for n in names if "@" not in n}
    generic = []
    for mat in ["PLA", "PETG", "ABS", "ASA", "TPU", "PA6", "PA", "PC"]:
        gname = f"Generic {mat}"
        if gname in plain or any(n.startswith(gname + " @") for n in names):
            generic.append({"material": mat, "name": gname, "filament_id": filament_id(gname),
                            "density": preset_density(remote_filament, gname)})
    return {"bambu": bambu, "generic": generic}


def resolve_machine_field(mpaths, name, key, seen=None):
    """Walk a machine preset's `inherits` chain until `key` appears (printable_area
    and printable_height often live in a shared @BBL parent, not the leaf)."""
    seen = seen or set()
    if not name or name in seen:
        return None
    seen.add(name)
    try:
        j = fetch_json(mpaths.get(name, f"BBL/machine/{name}.json"))
    except Exception:
        return None
    if key in j:
        return j[key]
    return resolve_machine_field(mpaths, j.get("inherits", ""), key, seen)


def gen_bed_sizes():
    """Bed W×D × height H (mm) per model from BBL.json machine presets
    (printable_area / printable_height, resolved through `inherits`). Prints a
    ready-to-paste web/user/src/api.ts BAMBU_BEDS block — the build-volume
    warning. Run: `python3 scripts/gen-bambu-maps.py --beds`."""
    mpaths = {e["name"]: "BBL/" + e["sub_path"] for e in fetch_json("BBL.json").get("machine_list", [])}
    print("== bed sizes (paste into web/user/src/api.ts BAMBU_BEDS) ==")
    for model in PRINTERS:
        dims = None
        for nz in NOZZLES:  # bed is nozzle-independent; try each until one resolves
            base = f"{model} {nz} nozzle"
            area = resolve_machine_field(mpaths, base, "printable_area")
            height = resolve_machine_field(mpaths, base, "printable_height")
            if area and height:
                xs, ys = [], []
                for p in area:
                    parts = re.split(r"[x,]", p)
                    xs.append(float(parts[0]))
                    ys.append(float(parts[1]))
                dims = (round(max(xs) - min(xs)), round(max(ys) - min(ys)), round(as_float(height)))
                break
        if dims:
            print(f'  "{model}": {{ w: {dims[0]}, d: {dims[1]}, h: {dims[2]} }},')
        else:
            print(f"  WARNING: no bed size for {model}", file=sys.stderr)


def gen_towers():
    """Per-model prime/purge-tower footprint + dual-nozzle flag. The tower
    width + brim come from the model's default process preset, resolved through
    its `inherits` chain like layer heights (they live in fdm_process_common
    today, so identical across models, but resolve per-model so a future override
    is picked up); the dual-nozzle flag comes from the machine preset's
    nozzle_diameter arity (an H2-class printer prints 2 filaments towerless).
    Prints a ready-to-paste web/user/src/api.ts BAMBU_TOWERS block — the
    multi-colour build-volume fit check. Run:
    `python3 scripts/gen-bambu-maps.py --towers`."""
    proc_paths = {e["name"]: "BBL/" + e["sub_path"] for e in fetch_json("BBL.json").get("process_list", [])}
    print("== prime towers (paste into web/user/src/api.ts BAMBU_TOWERS) ==")
    for model in PRINTERS:
        process, dual = "", False
        for nz in NOZZLES:  # the tower default is nozzle-independent; take the first machine that resolves
            try:
                j = fetch_json(f"BBL/machine/{model} {nz} nozzle.json")
            except Exception:
                continue
            process = j.get("default_print_profile", "")
            dual = len(j.get("nozzle_diameter", ["0.4"])) > 1
            if process:
                break
        width = as_float(resolve_process_field(proc_paths, process, "prime_tower_width"))
        brim = as_float(resolve_process_field(proc_paths, process, "prime_tower_brim_width"))
        if width is None:
            print(f"  WARNING: no prime_tower_width for {model} — assuming 35", file=sys.stderr)
            width = 35.0
        if brim is None:
            brim = 0.0
        print(f'  "{model}": {{ width: {width:g}, brim: {brim:g}, dual: {"true" if dual else "false"} }},')


def gen_qualities():
    """Per-printer×nozzle quality ladder (distinct layer heights + the nozzle's
    default), for the client-side tier→mm resolver (Settings shows the live
    resolved mm). Reads the ALREADY-GENERATED bambu_printer_map.json — the ladder
    is the `processes` there — so NO network. Mirror of the server's
    `tier_target_mm` inputs. Prints a ready-to-paste web/user/src/api.ts
    BAMBU_QUALITY block. Run: `python3 scripts/gen-bambu-maps.py --qualities`."""
    m = json.loads((PRESETS / "bambu_printer_map.json").read_text())
    print("== quality ladders (paste into web/user/src/api.ts BAMBU_QUALITY) ==")
    for e in m:
        parts = []
        for n in e["nozzles"]:
            heights = sorted({round(p["layer_mm"], 3) for p in n.get("processes", [])})
            if not heights:
                continue
            default = next(
                (round(p["layer_mm"], 3) for p in n["processes"] if p["process_id"] == n["process_id"]),
                heights[0],
            )
            hs = ", ".join(f"{h:g}" for h in heights)
            parts.append(f'"{n["nozzle"]}": {{ heights: [{hs}], default: {default:g} }}')
        if parts:
            print(f'  "{e["model"]}": {{ {", ".join(parts)} }},')


# The keys the export writes for a file with a slot OrcaSlicer has no
# preset for, in the order it writes them. Must equal stl3mf::ORCA_FALLBACK_KEYS
# (the Rust guard compares every entry's keys with it).
ORCA_FALLBACK_KEYS = [
    "nozzle_temperature",
    "nozzle_temperature_initial_layer",
    "nozzle_temperature_range_low",
    "nozzle_temperature_range_high",
    "cool_plate_temp",
    "cool_plate_temp_initial_layer",
    "eng_plate_temp",
    "eng_plate_temp_initial_layer",
    "hot_plate_temp",
    "hot_plate_temp_initial_layer",
    "textured_plate_temp",
    "textured_plate_temp_initial_layer",
    "supertack_plate_temp",
    "supertack_plate_temp_initial_layer",
]
# Bambu Studio 2.x presets carry one value per extruder variant
# (`filament_extruder_variant`); Filafy's file carries one per slot, so a slot
# takes the standard nozzle's value, the variant every Bambu template lists first.
STANDARD_VARIANT = "Direct Drive Standard"


def fetch_url_json(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def bambu_filament_flat(name, paths, stack=()):
    """A Bambu Studio filament preset flattened the way the app loads it: the
    parent (its `inherits`, recursively) < each `include` template < its own
    keys. `paths` maps a preset name to its repo path (from BBL.json)."""
    if name in stack:
        raise ValueError(f"inherits loop at {name}")
    j = fetch_json(paths.get(name, f"BBL/filament/{name}.json"))
    out = {}
    if j.get("inherits"):
        out.update(bambu_filament_flat(j["inherits"], paths, stack + (name,)))
    for inc in j.get("include") or []:
        out.update(bambu_filament_flat(inc, paths, stack + (name,)))
    out.update(j)
    return out


def standard_value(flat, key):
    """The value one slot takes for `key`: the standard-nozzle variant's entry
    (index 0 when the preset lists no variants, or the key is single-valued)."""
    v = flat.get(key)
    if v is None:
        return None
    v = v if isinstance(v, list) else [v]
    if not v:
        return None
    variants = flat.get("filament_extruder_variant") or []
    i = variants.index(STANDARD_VARIANT) if STANDARD_VARIANT in variants else 0
    return str(v[i] if i < len(v) else v[0])


def gen_orca_missing(fmap, pmap, tag=None):
    """The Bambu filament presets Filafy can name that OrcaSlicer's
    release `tag` (default: the latest release) does not bundle, on the printers
    that release supports, plus Bambu Studio's temperatures for each of them and
    for every preset that can share a file with one (same material, same printer
    code: the export never mixes materials, invariant 2). The export writes those
    temperatures for every slot of such a file, each slot its own preset's.

    Names follow stl3mf::slot_preset_entry exactly: "{name} @BBL {code}" when the
    entry has that code, else the material's Generic preset."""
    tag = tag or fetch_url_json(ORCA_LATEST)["tag_name"]
    orca = fetch_url_json(ORCA_RAW.format(tag=tag) + "BBL.json")
    orca_names = {e["name"] for e in orca.get("filament_list", [])}
    orca_models = {e["name"] for e in orca.get("machine_model_list", [])}
    codes = {}
    for e in pmap:
        codes.setdefault(e["code"], []).append(e["model"])
    supported = sorted(c for c, models in codes.items() if any(m in orca_models for m in models))
    unsupported = sorted(set(codes) - set(supported))
    print(f"  OrcaSlicer {tag}: {len(orca_names)} BBL filament presets; printer codes it has: "
          f"{', '.join(supported)}; without: {', '.join(unsupported) or 'none'}")

    def generic(material):
        g = next((g for g in fmap["generic"] if g["material"].lower() == material.lower()), None)
        return g["name"] if g else f"Generic {material}"

    def written(material, code):
        """Every preset name a slot of `material` can carry on `code`."""
        names = {f"{e['name']} @BBL {code}" for e in fmap["bambu"]
                 if e["material"].lower() == material.lower() and code in e["codes"]}
        return names | {generic(material)}

    bbl = fetch_json("BBL.json")
    paths = {e["name"]: "BBL/" + e["sub_path"] for e in bbl.get("filament_list", [])}
    missing = {}  # name -> [(material, code), ...]
    neither = set()
    for code in supported:
        for material in sorted({e["material"] for e in fmap["bambu"]} | {g["material"] for g in fmap["generic"]}):
            for name in sorted(written(material, code)):
                if name not in paths:
                    # Bambu Studio lacks it too ("Generic PA6": the map has no
                    # Generic row for that material), so it is no Orca gap.
                    neither.add(name)
                elif name not in orca_names:
                    missing.setdefault(name, []).append((material, code))
    for name in sorted(neither):
        print(f"  note: neither app bundles {name!r} (not an Orca gap; not listed)", file=sys.stderr)
    temps = {}
    for name in sorted(missing):
        for material, code in missing[name]:
            for other in sorted(written(material, code)):
                if other in temps:
                    continue
                try:
                    flat = bambu_filament_flat(other, paths)
                except Exception as e:  # noqa: BLE001 - reported, then decided below
                    flat, err = None, e
                values = {k: standard_value(flat, k) for k in ORCA_FALLBACK_KEYS} if flat else {}
                if flat is None or None in values.values():
                    gap = err if flat is None else [k for k, v in values.items() if v is None]
                    if other in missing:
                        sys.exit(f"  ERROR: Orca lacks {other!r} but its Bambu temperatures do not resolve: {gap}")
                    print(f"  WARNING: no Bambu temperatures for {other!r} ({gap}); a file with it writes none",
                          file=sys.stderr)
                    continue
                temps[other] = values
    for name in sorted(missing):
        t = temps[name]
        print(f"  Orca lacks {name:30s} nozzle {t['nozzle_temperature']} / first layer "
              f"{t['nozzle_temperature_initial_layer']}, textured plate {t['textured_plate_temp']}")
    if not missing:
        print("  Orca bundles every preset Filafy names on its printers: the list is empty")
    return {
        "orca_release": tag,
        "bambu_profiles": bbl.get("version", ""),
        "missing": sorted(missing),
        "temperatures": {n: temps[n] for n in sorted(temps)},
    }


def write_orca_missing(fmap, pmap, tag=None):
    out = gen_orca_missing(fmap, pmap, tag)
    (PRESETS / "bambu_orca_missing.json").write_text(json.dumps(out, indent=1) + "\n")


def main():
    if "--machine-presets" in sys.argv:
        use_local_profiles(sys.argv, "--machine-presets")
        write_machine_presets()
        return
    if "--check" in sys.argv:
        use_local_profiles(sys.argv, "--check")
        check_machine_presets()
        return
    if "--orca-missing" in sys.argv:
        i = sys.argv.index("--orca-missing")
        tag = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-") else None
        print("== presets OrcaSlicer lacks ==")
        write_orca_missing(json.loads((PRESETS / "bambu_filament_map.json").read_text()),
                           json.loads((PRESETS / "bambu_printer_map.json").read_text()), tag)
        print("\nwrote bambu_orca_missing.json — run `cargo test` to validate")
        return
    if "--densities" in sys.argv:
        fill_densities(sys.argv[sys.argv.index("--densities") + 1])
        return
    if "--beds" in sys.argv:
        gen_bed_sizes()
        return
    if "--towers" in sys.argv:
        gen_towers()
        return
    if "--qualities" in sys.argv:
        gen_qualities()
        return
    print("== printer map ==")
    printer_map = gen_printer_map()
    (PRESETS / "bambu_printer_map.json").write_text(json.dumps(printer_map, indent=1) + "\n")
    print("== filament map ==")
    filament_map = gen_filament_map()
    (PRESETS / "bambu_filament_map.json").write_text(json.dumps(filament_map, indent=1) + "\n")
    write_machine_presets()
    print("== presets OrcaSlicer lacks ==")
    write_orca_missing(filament_map, printer_map)
    print("\nwrote bambu_printer_map.json + bambu_filament_map.json + bambu_orca_missing.json"
          " — run `cargo test` to validate")


if __name__ == "__main__":
    main()
