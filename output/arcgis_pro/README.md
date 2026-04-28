# Fremont, NE — Census Tract 9642 · ArcGIS Pro replication package

This package contains everything needed to replicate the published
poverty-context map of Census Tract 9642 in Fremont, Nebraska inside
ArcGIS Pro (3.0 or newer).

## Contents

```
arcgis_pro/
├── README.md                    ← this file
├── symbology.json               ← machine-readable symbology spec
├── data/                        ← ESRI shapefiles (NAD83 / UTM 14N, EPSG:26914)
│   ├── ct9642_focus_tract/
│   ├── neighborhood_tracts_acs/
│   ├── fremont_place/
│   ├── dodge_county/
│   ├── roads_clipped/
│   ├── water_area/
│   ├── water_linear/
│   └── map_extent/
└── layers/                      ← .lyrx layer files (drag & drop into Pro)
    ├── 01_water_area.lyrx
    ├── 02_water_linear.lyrx
    ├── 03_neighborhood_tracts_acs.lyrx
    ├── 04_roads.lyrx
    ├── 05_dodge_county.lyrx
    ├── 06_fremont_place.lyrx
    ├── 07_ct9642_focus_tract.lyrx
    └── 08_map_extent.lyrx
```

The leading number on each `.lyrx` file is the recommended draw order
(top of the list is drawn last and sits on top of the map).

## Quick-start (5 minutes)

1. **Create a new project** in ArcGIS Pro → *Map* template.
2. Set the map's **Coordinate System** to *NAD 1983 UTM Zone 14N
   (EPSG:26914)*. (Map → Properties → Coordinate Systems →
   *Layers* → *Projected Coordinate Systems* → *UTM* → *NAD 1983* →
   *NAD 1983 UTM Zone 14N*.)
3. In *Catalog* pane, navigate to this folder and drag every `.lyrx`
   file in the `layers/` folder onto the map, **bottom-to-top in the
   order listed above** (`01_…` first, `08_…` last).
4. Each `.lyrx` references the matching shapefile in `../data/` using
   a relative path. If ArcGIS Pro asks you to *Set Data Source*,
   point at the shapefile of the same name in `data/`.
5. Insert → *Layout* → *Letter Landscape*. Add a Map Frame and
   point it at this map.
6. Insert → *North Arrow*, *Scale Bar* (miles, 2 mi), *Legend*,
   *Title text*. The accompanying `symbology.json` lists font sizes
   used in the published PNG/PDF if you want pixel-level fidelity.
7. *Share* → *Export Layout* → PDF.

## Reproducing the choropleth class breaks

Layer `neighborhood_tracts_acs` already ships with a
*Graduated Colors* renderer using **manual** class breaks on the
`pov_rate` field (computed as `B17001_002E / B17001_001E * 100`):

| Class | Upper bound | Hex color |
|------:|:-----------:|:---------:|
| < 5 % | 5 | `#fff7d6` |
| 5 – 10 % | 10 | `#fee08b` |
| 10 – 15 % | 15 | `#fdae61` |
| 15 – 20 % | 20 | `#f46d43` |
| 20 – 30 % | 30 | `#d73027` |
| ≥ 30 % | 100 | `#7a0177` |

To change the classification scheme, open Symbology → *Method* →
choose *Natural Breaks (Jenks)*, *Quantile*, *Equal Interval*, or
*Standard Deviation*; ArcGIS Pro will recompute breaks from the
`pov_rate` field and the colors will follow the ramp.

## Reproducing the road hierarchy

Layer `roads_clipped` ships with a *Unique Values* renderer keyed on
the TIGER `MTFCC` field. The drawn classes are:

| MTFCC | Class | Color | Width (pt) | Cased? |
|:-----:|:------|:------|:-----------|:------:|
| S1100 | Primary highway       | `#5b524a` | 2.4 | ✓ (white case) |
| S1200 | Secondary highway     | `#7a7164` | 1.5 | ✓ |
| S1400 | Local road            | `#cfc8ba` | 0.6 |   |
| S1630 | Ramp / connector      | `#7a7164` | 1.2 | ✓ |
| S1500 | Vehicular trail       | `#cfc8ba` | 0.5 |   |
| S1730 | Alley                 | `#cfc8ba` | 0.4 |   |
| S1740 | Service drive         | `#cfc8ba` | 0.4 |   |
| S1750 | Private road          | `#cfc8ba` | 0.4 |   |

ArcGIS Pro draws cased lines using two stroke layers in the symbol
(see *Properties → Symbology → Layers*).

## Other layers

* **`fremont_place`** — single dashed stroke, color `#1f4e6b`, 1.7 pt.
* **`dodge_county`** — single solid stroke, color `#5e574a`, 1.3 pt.
* **`water_area`** — fill `#aac9e0` (90 % opacity), edge `#7fa7c4`, 0.4 pt.
* **`water_linear`** — stroke `#7fa7c4`, 0.6 pt.
* **`ct9642_focus_tract`** — fill `#1f6feb` (45 % opacity), edge `#0a2540`, 2.4 pt.

## Coordinate system, sources, and update cadence

* Projected CRS: NAD83 / UTM Zone 14N — EPSG:26914.
* Sources:
  - U.S. Census Bureau **TIGER/Line Shapefiles 2024** (tracts,
    places, counties, states, roads, areawater, linearwater).
  - U.S. Census Bureau **American Community Survey 2024
    5-Year Estimates**, table B17001 (poverty status by sex by age).
* The Python pipeline that produced these files lives in
  `scripts/create_fremont_ct9642_map.py` of this repository. Re-run
  with `python3 scripts/create_fremont_ct9642_map.py` to refresh
  shapefiles, then `python3 scripts/build_arcgis_pro_package.py` to
  rebuild this package.
