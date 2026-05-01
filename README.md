# Map Requests

Reproducible cartography for U.S. census-geography requests. Each map
script downloads authoritative public data and produces a publication
PNG, a vector PDF, ESRI shapefiles, and an ArcGIS Pro replication
package — all from one command.

## Fremont, Nebraska — Census Tract 9642

A professional context map centered on Census Tract 9642 in Fremont,
Dodge County, Nebraska. The map shows the tract footprint over a
neighborhood-tract poverty choropleth, with cased road hierarchy,
hydrography, locator inset, scale bar, and north arrow.

### Build everything

```bash
python3 -m pip install -r requirements.txt
python3 scripts/create_fremont_ct9642_map.py
python3 scripts/build_arcgis_pro_package.py
```

The first command produces the rendered map and shapefiles; the second
assembles the ArcGIS Pro package on top of those shapefiles.

`create_fremont_ct9642_map.py` accepts:

```text
--output-dir DIR     Override the output directory (default: ./output)
--basename NAME      Filename stem for the rendered PNG/PDF
--no-shapefiles      Skip shapefile exports
```

### Outputs

All artifacts are written to `output/`:

| Path | Format | Description |
| --- | --- | --- |
| `fremont_ne_ct9642_poverty_map.png` | PNG (200 dpi) | Final map for screen and presentations. |
| `fremont_ne_ct9642_poverty_map.pdf` | Vector PDF | Print-ready output, scales without quality loss. |
| `shapefiles/<layer>/<layer>.shp` | ESRI Shapefile | Each map layer exported individually. |
| `fremont_ne_ct9642_layers.zip` | ZIP | Bundle of all shapefiles. |
| `arcgis_pro/` | Folder | ArcGIS Pro replication package — shapefiles, `.lyrx` layer files, README, symbology spec. |
| `fremont_ne_ct9642_arcgis_pro_package.zip` | ZIP | Same package zipped for distribution. |

Shapefile layers (NAD83 / UTM Zone 14N, EPSG:26914):

- `ct9642_focus_tract` — the highlighted Census Tract 9642 polygon.
- `neighborhood_tracts_acs` — Dodge County tracts intersecting the map
  extent, joined with ACS B17001 poverty estimates.
- `fremont_place` — City of Fremont place boundary.
- `dodge_county` — Dodge County boundary.
- `roads_clipped` — TIGER/Line roads in the map extent (with MTFCC).
- `water_area` — TIGER area water (lakes, river polygons).
- `water_linear` — TIGER linear water (streams, river centerlines).
- `map_extent` — rectangular extent of the rendered map.

### ArcGIS Pro replication package

`output/arcgis_pro/` contains everything a GIS analyst needs to rebuild
the published map inside ArcGIS Pro 3.0+. Drag the numbered `.lyrx`
files from `layers/` onto a new map (bottom-to-top in numeric order)
and the symbology — graduated colors for the poverty choropleth,
unique values for the road hierarchy, dashed city boundary, etc. — is
applied automatically. The bundled `README.md` and `symbology.json`
document the design (fonts, sizes, hex codes, class breaks) for
pixel-level fidelity.

### Data sources

- U.S. Census Bureau **TIGER/Line Shapefiles 2024** — tracts, places,
  counties, states, roads, areawater, linearwater.
- U.S. Census Bureau **American Community Survey 2024 5-Year
  Estimates**, table B17001 (poverty status by sex by age).

Both are public-domain federal data products. The script caches the
ACS JSON response under `data/` to avoid repeat downloads.
