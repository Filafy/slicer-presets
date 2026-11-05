# slicer-presets

Printer, process and filament settings that [Filafy](https://filafy.app) writes into
the 3MF files its members download, and the scripts that generate them.

Filafy turns images into multi-color prints. A 3MF only opens with the right presets
in a slicer if it carries the slicer's own values, so Filafy reads those values from
the slicers' profiles and stores them here. Parts of this data are G-code templates.

Filafy's server is closed source. It reads these files at run time and does not
compile them in. Each exported 3MF names this repository, at the commit that build
uses, in its `LicenseTerms` metadata. Filafy's CI fails if the files it ships differ
from that commit by a byte.

## What is here

| Path | What it is |
|---|---|
| `presets/` | The generated maps, exactly as Filafy reads them. |
| `scripts/gen-*-maps.py` | The generators. Python 3, standard library only. |
| `LICENSE` | The GNU Affero General Public License, version 3. |

## License

This repository is offered under the GNU Affero General Public License, version 3
only (AGPL-3.0-only). The scripts are Filafy's own work.

The profile data in `presets/` comes from the projects below and stays under the
terms those projects set. The table says what we found, not what we concluded. Where
a file carries no license statement of its own, Filafy cannot grant you one.

## Where each file came from

Every version below is the one read from the app on the day the map was generated.
Each app's own profile version is in the map's `source` or `meta` field. "Repository
license" is what the project's public repository shows at its top level. The maps
were read from the installed apps, not from those repositories, and we did not compare
the two. The `resources/profiles` folders we listed have no license file at their top
level.

| Map | Upstream | Version read | What came from it | License as found |
|---|---|---|---|---|
| `bambu_printer_map.json`, `bambu_filament_map.json`, `bambu_preset_compat.json` | [Bambu Studio](https://github.com/bambulab/BambuStudio) | profiles 02.09.00.74 | Preset names, filament ids, density, layer heights, line widths, which presets each machine lists | Repository: AGPL-3.0 |
| `bambu_orca_missing.json` | Bambu Studio and [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) | Bambu profiles 02.08.00.09, OrcaSlicer v2.4.2 | Which Bambu filament presets OrcaSlicer lacks, and their temperatures | Both repositories: AGPL-3.0 |
| `anycubic_map.json.gz` | [Anycubic Slicer Next](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext) | app 2.0.0.3, profiles 1.3.2609.02 | Whole resolved machine, process and filament configs, with G-code templates | Repository: AGPL-3.0 |
| `creality_map.json.gz` | [Creality Print](https://github.com/CrealityOfficial/CrealityPrint) | app 7.3.0.6157, profiles 26.10.10.09 | Whole resolved configs, with G-code templates | Repository: AGPL-3.0 |
| `orca_creality_map.json.gz` | OrcaSlicer, its Creality vendor set | app 2.4.2, profiles 02.03.02.75 | Whole resolved configs, with G-code templates | Repository: AGPL-3.0 |
| `elegoo_map.json.gz` | [ElegooSlicer](https://github.com/elegooofficial/ElegooSlicer) | app 1.5.3.5, profiles 01.05.03.05 | Whole resolved configs, with G-code templates | Repository: AGPL-3.0 |
| `elegoo_orca_map.json.gz` | OrcaSlicer, its Elegoo vendor set | app 2.4.2, profiles 02.04.00.06 | Whole resolved configs, with G-code templates | Repository: AGPL-3.0 |
| `snapmaker_map.json.gz` | [Snapmaker Orca](https://github.com/Snapmaker/OrcaSlicer) | app 2.4.0, profiles 02.03.03.03 | Whole resolved configs, with G-code templates | Repository: AGPL-3.0 |
| `prusa_printer_map.json.gz`, `prusa_filament_map.json.gz` | [PrusaSlicer](https://github.com/prusa3d/PrusaSlicer) vendor profiles, resolved by PrusaSlicer 2.9.6 | `PrusaResearch.ini` 2.5.5 | Whole resolved configs, with G-code templates | **The file Filafy read carries no license statement of its own.** Version 2.5.5 came from the app's updater and is published in [PrusaSlicer-settings-prusa-fff](https://github.com/prusa3d/PrusaSlicer-settings-prusa-fff), which shows no license; [issue 36](https://github.com/prusa3d/PrusaSlicer-settings-prusa-fff/issues/36) there asks and had no answer on 2026-11-05. Version 2.4.14 of the same file is in the AGPL-3.0 [PrusaSlicer](https://github.com/prusa3d/PrusaSlicer) repository at `resources/profiles/PrusaResearch.ini` (tags `version_2.9.6` and `version_2.9.7`), and 90% of the lines of 2.5.5 are identical to it. The older [PrusaSlicer-settings](https://github.com/prusa3d/PrusaSlicer-settings) repository is AGPL-3.0. |

A few values are not printer settings at all. `machine_base.print_host` in both
Creality maps is the address `http://172.23.209.71:4408/`, as it stands in the
vendors' own profiles. It is kept so the maps match them.

## How to regenerate

Each script reads the slicer installed on the machine, on macOS, in the path at the
top of the script, and writes to `presets/`. Each one's docstring explains what it
reads and the traps it guards against.

```bash
python3 scripts/gen-snapmaker-maps.py
python3 scripts/gen-anycubic-maps.py
python3 scripts/gen-creality-maps.py --app creality
python3 scripts/gen-creality-maps.py --app orca     # needs OrcaSlicer 2.4.2
python3 scripts/gen-elegoo-maps.py                  # needs OrcaSlicer 2.4.2
python3 scripts/gen-prusa-maps.py --seed-3mf FILE   # FILE: any 3MF or STL
python3 scripts/gen-bambu-maps.py                   # reads Bambu's repository over the network
```

Run here with the same app versions, the Snapmaker and Creality Print maps came out
byte-identical, and the Anycubic map differed only in the date in its `source` field.
The other maps were not regenerated that way. The Prusa, Bambu and Creality scripts
name their own printers. In a Filafy checkout the others read the printer list from Filafy's server
source, and here they use the list already in the map. A flag that prints a block for
Filafy's own client code (`--beds`, for one) can need that source.

## Not affiliated

Filafy is not affiliated with, endorsed by or sponsored by Bambu Lab, the OrcaSlicer
project, Anycubic, Snapmaker, Elegoo, Creality or Prusa Research. Their names appear
here only to say where the data came from.
