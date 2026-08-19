# Census tracts of Fremont, Nebraska

Print-ready reference map of every **2020 U.S. Census tract** that intersects the incorporated city of Fremont, Nebraska.

## Deliverable

- [`output/fremont_ne_census_tracts.pdf`](output/fremont_ne_census_tracts.pdf) — vector PDF, tabloid landscape (17 × 11 in)

A PNG preview is written alongside the PDF for review.

## Geography

Fremont is Census place **GEOID 3117670** (`Fremont city`) in Dodge County, Nebraska (state FIPS `31`, county FIPS `053`). Tract polygons follow the **2020 Census** definitions published in TIGER/Line 2024.

A tract is included if it **intersects** the city limit. Colored fills are **clipped to the place boundary**; dashed lines show where a tract continues outside the city. That is the standard way to map “tracts within a city” without implying that statistical units stop at the municipal line.

| Tract | GEOID | Area inside city | Share of tract in city |
| --- | --- | --- | --- |
| 9638 | 31053963800 | 2.93 sq mi | 5.6% |
| 9639 | 31053963900 | 1.64 sq mi | 91.3% |
| 9640 | 31053964000 | 0.67 sq mi | 100% |
| 9641 | 31053964100 | 1.54 sq mi | 78.2% |
| 9642 | 31053964200 | 0.95 sq mi | 100% |
| 9643 | 31053964300 | 1.75 sq mi | 49.3% |
| 9644 | 31053964400 | 1.62 sq mi | 12.3% |

Tracts 9636 and 9637 (Dodge County) do not intersect Fremont and are omitted.

## How to regenerate

```bash
python3 -m pip install -r requirements.txt
python3 scripts/create_fremont_census_tracts_map.py
```

TIGER/Line and cartographic-boundary downloads are cached under `data/` (gitignored).

## Cartography

- Projection: NAD83 / Nebraska State Plane (EPSG:32104), meters; north arrow is grid north
- Qualitative, colorblind-safe tract fills (muted Okabe–Ito)
- Road hierarchy from TIGER MTFCC (`S1100` / `S1200` / `S1400`)
- Hydrography from TIGER area and linear water
- Locator inset: Nebraska counties with Dodge County highlighted
- Scale bar in miles

## Sources

U.S. Census Bureau, TIGER/Line Shapefiles (2024) and Cartographic Boundary Files (20 million-scale, locator).
