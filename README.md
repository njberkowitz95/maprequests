# Map Requests

This repository contains reproducible map-generation scripts.

## Fremont, Nebraska - Census Tract 9642

Generate a cartographic context map centered on Census Tract 9642 in Fremont,
Dodge County, Nebraska:

```bash
python3 -m pip install -r requirements.txt
python3 scripts/create_fremont_ct9642_map.py
```

The script downloads U.S. Census Bureau TIGER/Line geometries and ACS 5-year
poverty estimates, then writes:

```text
output/fremont_ne_ct9642_poverty_map.png
```