# Map Requests

Reproducible cartographic scripts that download authoritative public data
and render publication-style maps along with vector PDFs and ESRI
shapefile exports for downstream GIS use.

## Fremont, Nebraska — Census Tract 9642

A professional context map centered on Census Tract 9642 in Fremont,
Dodge County, Nebraska, with a tract-level ACS poverty classification
overlay, locator inset, scale bar, and north arrow.

### Run

```bash
python3 -m pip install -r requirements.txt
python3 scripts/create_fremont_ct9642_map.py
```

Optional flags:

```text
--output-dir DIR     Override the output directory (default: ./output)
--basename NAME      Filename stem for the rendered PNG/PDF
--no-shapefiles      Skip shapefile exports
```

### Outputs

All artifacts are written to `output/`:

| File | Format | Description |
| --- | --- | --- |
| `fremont_ne_ct9642_poverty_map.png` | PNG raster (200 dpi) | Final map for screen and presentations. |
| `fremont_ne_ct9642_poverty_map.pdf` | Vector PDF | Print-ready output suitable for resizing without quality loss. |
| `shapefiles/<layer>/<layer>.shp` | ESRI Shapefile | Each map layer exported individually (see below). |
| `fremont_ne_ct9642_layers.zip` | ZIP archive | Bundle of all shapefiles for convenient distribution. |

Shapefile layers (all in NAD83 / UTM Zone 14N, EPSG:26914):

* `ct9642_focus_tract` — the highlighted Census Tract 9642 polygon.
* `neighborhood_tracts_acs` — Dodge County tracts intersecting the map
  extent, joined with ACS B17001 poverty estimates.
* `fremont_place` — the City of Fremont place boundary.
* `dodge_county` — the Dodge County boundary.
* `roads_clipped` — TIGER/Line roads within the map extent (with MTFCC).
* `map_extent` — the rectangular extent of the rendered map.

### Data sources

* U.S. Census Bureau TIGER/Line Shapefiles 2024 (tracts, places,
  counties, states, roads).
* American Community Survey 2024 5-Year Estimates, table B17001
  (poverty status by sex by age).

Both are public-domain federal data products. The script caches the ACS
JSON response in `data/` to avoid repeat downloads.
