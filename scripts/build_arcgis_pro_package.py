#!/usr/bin/env python3
"""Assemble an ArcGIS Pro–ready replication package.

Run after :mod:`scripts.create_fremont_ct9642_map`. This script:

1. Copies the produced shapefiles into ``output/arcgis_pro/data/``.
2. Generates a ``.lyrx`` layer file for each layer with symbology that
   reproduces the published map (class breaks for the poverty
   choropleth, unique values for the road hierarchy, simple symbols
   elsewhere).
3. Writes a step-by-step ``README.md`` and a ``symbology.json`` spec so
   the design is reproducible without ArcGIS Pro at hand.

The ``.lyrx`` schema follows the public ArcGIS Pro CIM layout for
``CIMLayerDocument`` (version 3.x). It is intentionally minimal but
opens cleanly in ArcGIS Pro 3.0+ ("Add Layer From File…" or simply
double-click in Catalog).
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any


SOURCE_SHP_DIR  = Path("output/shapefiles")
PACKAGE_DIR     = Path("output/arcgis_pro")
DATA_DIR        = PACKAGE_DIR / "data"
LYRX_DIR        = PACKAGE_DIR / "layers"
ARCHIVE_PATH    = Path("output/fremont_ne_ct9642_arcgis_pro_package.zip")

# Symbology definitions — kept in lock-step with the matplotlib palette
# in scripts/create_fremont_ct9642_map.py.
PALETTE: dict[str, str] = {
    "focus_fill":  "#1f6feb",
    "focus_edge":  "#0a2540",
    "city_edge":   "#1f4e6b",
    "county_edge": "#5e574a",
    "highway":     "#5b524a",
    "arterial":    "#7a7164",
    "local":       "#cfc8ba",
    "water_fill":  "#aac9e0",
    "water_edge":  "#7fa7c4",
    "extent_edge": "#0a2540",
}

POVERTY_CLASSES = [
    {"upper":   5.0, "label": "< 5%",      "color": "#fff7d6"},
    {"upper":  10.0, "label": "5 – 10%",   "color": "#fee08b"},
    {"upper":  15.0, "label": "10 – 15%",  "color": "#fdae61"},
    {"upper":  20.0, "label": "15 – 20%",  "color": "#f46d43"},
    {"upper":  30.0, "label": "20 – 30%",  "color": "#d73027"},
    {"upper": 100.0, "label": "≥ 30%",     "color": "#7a0177"},
]

ROAD_CLASSES = [
    {"mtfcc": "S1100", "label": "Primary highway",     "color": PALETTE["highway"],  "width": 2.4, "case": True,  "case_width": 4.0},
    {"mtfcc": "S1200", "label": "Secondary highway",   "color": PALETTE["arterial"], "width": 1.5, "case": True,  "case_width": 2.6},
    {"mtfcc": "S1400", "label": "Local road",          "color": PALETTE["local"],    "width": 0.6, "case": False, "case_width": 0.0},
    {"mtfcc": "S1630", "label": "Ramp / connector",    "color": PALETTE["arterial"], "width": 1.2, "case": True,  "case_width": 2.2},
    {"mtfcc": "S1500", "label": "Vehicular trail",     "color": PALETTE["local"],    "width": 0.5, "case": False, "case_width": 0.0},
    {"mtfcc": "S1730", "label": "Alley",               "color": PALETTE["local"],    "width": 0.4, "case": False, "case_width": 0.0},
    {"mtfcc": "S1740", "label": "Service drive",       "color": PALETTE["local"],    "width": 0.4, "case": False, "case_width": 0.0},
    {"mtfcc": "S1750", "label": "Private road",        "color": PALETTE["local"],    "width": 0.4, "case": False, "case_width": 0.0},
]


# --------------------------------------------------------------------------- #
# CIM helpers                                                                 #
# --------------------------------------------------------------------------- #

def hex_to_rgb(hex_color: str) -> list[int]:
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def cim_rgb(hex_color: str, alpha: int = 100) -> dict[str, Any]:
    r, g, b = hex_to_rgb(hex_color)
    return {"type": "CIMRGBColor", "values": [r, g, b, alpha]}


def cim_solid_stroke(color_hex: str, width_pt: float, *, alpha: int = 100) -> dict[str, Any]:
    return {
        "type": "CIMSolidStroke",
        "enable": True,
        "capStyle": "Round",
        "joinStyle": "Round",
        "lineStyle3D": "Strip",
        "miterLimit": 10,
        "width": width_pt,
        "color": cim_rgb(color_hex, alpha),
    }


def cim_solid_fill(color_hex: str, *, alpha: int = 100) -> dict[str, Any]:
    return {
        "type": "CIMSolidFill",
        "enable": True,
        "color": cim_rgb(color_hex, alpha),
    }


def cim_polygon_symbol(
    fill_hex: str | None,
    stroke_hex: str,
    stroke_width_pt: float,
    *,
    fill_alpha: int = 100,
    stroke_alpha: int = 100,
    stroke_dash: list[float] | None = None,
) -> dict[str, Any]:
    layers: list[dict[str, Any]] = []
    stroke = cim_solid_stroke(stroke_hex, stroke_width_pt, alpha=stroke_alpha)
    if stroke_dash:
        stroke["effects"] = [{
            "type": "CIMGeometricEffectDashes",
            "dashTemplate": stroke_dash,
            "lineDashEnding": "NoConstraint",
        }]
    layers.append(stroke)
    if fill_hex is not None:
        layers.append(cim_solid_fill(fill_hex, alpha=fill_alpha))
    return {
        "type": "CIMPolygonSymbol",
        "symbolLayers": layers,
    }


def cim_line_symbol(
    color_hex: str,
    width_pt: float,
    *,
    case_color_hex: str | None = None,
    case_width_pt: float | None = None,
    alpha: int = 100,
    dash: list[float] | None = None,
) -> dict[str, Any]:
    layers: list[dict[str, Any]] = []
    # Cased lines: draw casing first (on top in the JSON list because
    # ArcGIS draws layers from bottom to top).
    primary = cim_solid_stroke(color_hex, width_pt, alpha=alpha)
    if dash:
        primary["effects"] = [{
            "type": "CIMGeometricEffectDashes",
            "dashTemplate": dash,
            "lineDashEnding": "NoConstraint",
        }]
    layers.append(primary)
    if case_color_hex is not None and case_width_pt is not None:
        layers.append(cim_solid_stroke(case_color_hex, case_width_pt))
    return {
        "type": "CIMLineSymbol",
        "symbolLayers": layers,
    }


def cim_symbol_reference(symbol: dict[str, Any]) -> dict[str, Any]:
    return {"type": "CIMSymbolReference", "symbol": symbol}


def feature_table(
    layer_name: str, dataset_name: str, *, dataset_type: str = "esriDTShapefile",
) -> dict[str, Any]:
    # Each shapefile lives in its own subfolder (data/<name>/<name>.shp), so
    # the workspace folder must include the subfolder name. ArcGIS Pro
    # resolves the relative path based on the .lyrx file location.
    return {
        "type": "CIMFeatureTable",
        "displayField": "GEOID" if "tract" in dataset_name.lower() else "FID",
        "editable": True,
        "dataConnection": {
            "type": "CIMStandardDataConnection",
            "workspaceConnectionString": f"DATABASE=..\\data\\{dataset_name}",
            "workspaceFactory": "Shapefile",
            "dataset": f"{dataset_name}.shp",
            "datasetType": dataset_type,
        },
        "studyAreaSpatialRel": "esriSpatialRelUndefined",
        "searchOrder": "esriSearchOrderSpatial",
    }


def feature_layer_skeleton(
    layer_name: str, dataset_name: str, *, geometry_type: str,
) -> dict[str, Any]:
    return {
        "type": "CIMFeatureLayer",
        "name": layer_name,
        "uRI": f"CIMPATH=Map/{dataset_name.lower()}.xml",
        "sourceModifiedTime": {"type": "TimeInstant"},
        "useSourceMetadata": True,
        "description": "",
        "layerElevation": {
            "type": "CIMLayerElevationSurface",
            "mapElevationID": "{8AC7B2D6-8E66-4F9A-9C40-2E1C36F3B3A2}",
        },
        "expanded": True,
        "layerType": "Operational",
        "showLegends": True,
        "visibility": True,
        "displayCacheType": "Permanent",
        "maxDisplayCacheAge": 5,
        "showPopups": True,
        "serviceLayerID": -1,
        "refreshRate": -1,
        "refreshRateUnit": "esriTimeUnitsSeconds",
        "blendingMode": "Alpha",
        "allowDrapingOnIntegratedMesh": True,
        "featureTable": feature_table(layer_name, dataset_name),
        "featureBlendingMode": "Alpha",
    }


def lyrx_document(layer_definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "CIMLayerDocument",
        "version": "3.0.0",
        "build": 36056,
        "layers": [layer_definition["uRI"]],
        "layerDefinitions": [layer_definition],
        "binaryReferences": [],
        "elevationSurfaceLayerDefinitions": [],
        "rGBColorProfile": "sRGB IEC61966-2.1",
        "cMYKColorProfile": "U.S. Web Coated (SWOP) v2",
    }


# --------------------------------------------------------------------------- #
# Renderers                                                                   #
# --------------------------------------------------------------------------- #

def renderer_simple(symbol: dict[str, Any], label: str) -> dict[str, Any]:
    return {
        "type": "CIMSimpleRenderer",
        "patch": "Default",
        "symbol": cim_symbol_reference(symbol),
        "label": label,
    }


def renderer_class_breaks_poverty() -> dict[str, Any]:
    breaks = []
    for cls in POVERTY_CLASSES:
        breaks.append({
            "type": "CIMClassBreak",
            "label": cls["label"],
            "patch": "Default",
            "symbol": cim_symbol_reference(
                cim_polygon_symbol(
                    cls["color"], "#a89f8e", 0.4, fill_alpha=70,
                )
            ),
            "upperBound": cls["upper"],
        })

    return {
        "type": "CIMClassBreaksRenderer",
        "barrierWeight": "High",
        "classBreakType": "GraduatedColor",
        "classificationMethod": "Manual",
        "field": "pov_rate",
        "minimumBreak": 0.0,
        "numberFormat": {
            "type": "CIMNumericFormat",
            "alignmentOption": "esriAlignRight",
            "alignmentWidth": 0,
            "roundingOption": "esriRoundNumberOfDecimals",
            "roundingValue": 1,
            "useSeparator": True,
        },
        "showInAscendingOrder": True,
        "heading": "Poverty rate (%)",
        "sampleSize": 10000,
        "useExclusionSymbol": False,
        "exclusionLabel": "<excluded>",
        "exclusionSymbol": cim_symbol_reference(
            cim_polygon_symbol("#ececec", "#bdbdbd", 0.4)
        ),
        "breaks": breaks,
        "polygonSymbolColorTarget": "Fill",
    }


def renderer_unique_values_roads() -> dict[str, Any]:
    classes = []
    for cls in ROAD_CLASSES:
        symbol = cim_line_symbol(
            cls["color"], cls["width"],
            case_color_hex="#ffffff" if cls["case"] else None,
            case_width_pt=cls["case_width"] if cls["case"] else None,
        )
        classes.append({
            "type": "CIMUniqueValueClass",
            "label": cls["label"],
            "patch": "Default",
            "symbol": cim_symbol_reference(symbol),
            "values": [{"type": "CIMUniqueValue", "fieldValues": [cls["mtfcc"]]}],
            "visible": True,
            "editable": True,
        })

    default = cim_line_symbol(PALETTE["local"], 0.5)
    return {
        "type": "CIMUniqueValueRenderer",
        "colorRamp": None,
        "defaultLabel": "Other",
        "defaultSymbol": cim_symbol_reference(default),
        "defaultSymbolPatch": "Default",
        "fields": ["MTFCC"],
        "groups": [{
            "type": "CIMUniqueValueGroup",
            "classes": classes,
            "heading": "Road classification (TIGER MTFCC)",
        }],
        "useDefaultSymbol": True,
        "polygonSymbolColorTarget": "Fill",
    }


# --------------------------------------------------------------------------- #
# Per-layer assembly                                                          #
# --------------------------------------------------------------------------- #

def write_lyrx(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, ensure_ascii=False)


def build_focus_tract_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "Focus area — Census Tract 9642", "ct9642_focus_tract",
        geometry_type="Polygon",
    )
    layer["renderer"] = renderer_simple(
        cim_polygon_symbol(
            PALETTE["focus_fill"], PALETTE["focus_edge"], 2.4,
            fill_alpha=45,
        ),
        "Census Tract 9642",
    )
    write_lyrx(out, lyrx_document(layer))


def build_neighborhood_tracts_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "Neighborhood tracts — ACS poverty rate",
        "neighborhood_tracts_acs",
        geometry_type="Polygon",
    )
    layer["renderer"] = renderer_class_breaks_poverty()
    layer["labelClasses"] = [{
        "type": "CIMLabelClass",
        "expression": "$feature.GEOID",
        "expressionEngine": "Arcade",
        "featuresToLabel": "AllVisibleFeatures",
        "name": "GEOID",
        "priority": -1,
        "standardLabelPlacementProperties": {
            "type": "CIMStandardLabelPlacementProperties",
            "featureType": "Polygon",
            "featureWeight": "Low",
            "labelWeight": "High",
            "numLabelsOption": "OneLabelPerName",
        },
        "textSymbol": {
            "type": "CIMSymbolReference",
            "symbol": {
                "type": "CIMTextSymbol",
                "blockProgression": "TTB",
                "depth3D": 1,
                "extrapolateBaselines": True,
                "fontEffects": "Normal",
                "fontEncoding": "Unicode",
                "fontFamilyName": "Calibri",
                "fontStyleName": "Regular",
                "fontType": "Unspecified",
                "haloSize": 1,
                "height": 6,
                "hinting": "Default",
                "horizontalAlignment": "Center",
                "kerning": True,
                "letterWidth": 100,
                "ligatures": True,
                "lineGapType": "ExtraLeading",
                "symbol": {
                    "type": "CIMPolygonSymbol",
                    "symbolLayers": [cim_solid_fill("#444444")],
                },
                "textCase": "Normal",
                "textDirection": "LTR",
                "verticalAlignment": "Bottom",
                "verticalGlyphOrientation": "Right",
                "wordSpacing": 100,
                "billboardMode3D": "FaceNearPlane",
            },
        },
        "visibility": False,
    }]
    write_lyrx(out, lyrx_document(layer))


def build_fremont_place_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "City of Fremont (place)", "fremont_place",
        geometry_type="Polygon",
    )
    layer["renderer"] = renderer_simple(
        cim_polygon_symbol(
            None, PALETTE["city_edge"], 1.7,
            stroke_dash=[5, 3],
        ),
        "City of Fremont",
    )
    write_lyrx(out, lyrx_document(layer))


def build_dodge_county_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "Dodge County boundary", "dodge_county",
        geometry_type="Polygon",
    )
    layer["renderer"] = renderer_simple(
        cim_polygon_symbol(None, PALETTE["county_edge"], 1.3),
        "Dodge County",
    )
    write_lyrx(out, lyrx_document(layer))


def build_roads_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "Roads (by TIGER MTFCC)", "roads_clipped",
        geometry_type="Polyline",
    )
    layer["renderer"] = renderer_unique_values_roads()
    write_lyrx(out, lyrx_document(layer))


def build_water_area_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "Water — area features", "water_area",
        geometry_type="Polygon",
    )
    layer["renderer"] = renderer_simple(
        cim_polygon_symbol(
            PALETTE["water_fill"], PALETTE["water_edge"], 0.4,
            fill_alpha=90,
        ),
        "Lakes & rivers",
    )
    write_lyrx(out, lyrx_document(layer))


def build_water_linear_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "Water — linear features", "water_linear",
        geometry_type="Polyline",
    )
    layer["renderer"] = renderer_simple(
        cim_line_symbol(PALETTE["water_edge"], 0.6),
        "Streams & connectors",
    )
    write_lyrx(out, lyrx_document(layer))


def build_extent_lyrx(out: Path) -> None:
    layer = feature_layer_skeleton(
        "Map extent (reference)", "map_extent",
        geometry_type="Polygon",
    )
    layer["renderer"] = renderer_simple(
        cim_polygon_symbol(None, PALETTE["extent_edge"], 1.0),
        "Map extent",
    )
    write_lyrx(out, lyrx_document(layer))


# --------------------------------------------------------------------------- #
# Documentation                                                               #
# --------------------------------------------------------------------------- #

README_TEMPLATE = """# Fremont, NE — Census Tract 9642 · ArcGIS Pro replication package

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
  - U.S. Census Bureau **TIGER/Line Shapefiles {ACS_YEAR}** (tracts,
    places, counties, states, roads, areawater, linearwater).
  - U.S. Census Bureau **American Community Survey {ACS_YEAR}
    5-Year Estimates**, table B17001 (poverty status by sex by age).
* The Python pipeline that produced these files lives in
  `scripts/create_fremont_ct9642_map.py` of this repository. Re-run
  with `python3 scripts/create_fremont_ct9642_map.py` to refresh
  shapefiles, then `python3 scripts/build_arcgis_pro_package.py` to
  rebuild this package.
"""


SYMBOLOGY_SPEC: dict[str, Any] = {
    "project_crs": "EPSG:26914",
    "datum": "NAD83",
    "units": "meters",
    "page_size_inches": [13.0, 9.5],
    "title_font": {"family": "Calibri", "size_pt": 20, "weight": "bold"},
    "subtitle_font": {"family": "Calibri", "size_pt": 11, "weight": "regular"},
    "label_font": {"family": "Calibri", "size_pt": 9, "weight": "regular"},
    "footer_font": {"family": "Calibri", "size_pt": 7.6, "weight": "regular"},
    "palette": PALETTE,
    "poverty_classes": POVERTY_CLASSES,
    "road_classes": ROAD_CLASSES,
}


# --------------------------------------------------------------------------- #
# Driver                                                                      #
# --------------------------------------------------------------------------- #

def copy_shapefiles() -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE_SHP_DIR.exists():
        raise FileNotFoundError(
            f"Expected shapefiles in {SOURCE_SHP_DIR}. "
            "Run scripts/create_fremont_ct9642_map.py first."
        )
    for layer_dir in sorted(SOURCE_SHP_DIR.iterdir()):
        if layer_dir.is_dir():
            shutil.copytree(layer_dir, DATA_DIR / layer_dir.name)


def write_layer_files() -> None:
    if LYRX_DIR.exists():
        shutil.rmtree(LYRX_DIR)
    LYRX_DIR.mkdir(parents=True, exist_ok=True)

    builders = [
        ("01_water_area.lyrx",                build_water_area_lyrx),
        ("02_water_linear.lyrx",              build_water_linear_lyrx),
        ("03_neighborhood_tracts_acs.lyrx",   build_neighborhood_tracts_lyrx),
        ("04_roads.lyrx",                     build_roads_lyrx),
        ("05_dodge_county.lyrx",              build_dodge_county_lyrx),
        ("06_fremont_place.lyrx",             build_fremont_place_lyrx),
        ("07_ct9642_focus_tract.lyrx",        build_focus_tract_lyrx),
        ("08_map_extent.lyrx",                build_extent_lyrx),
    ]
    for name, builder in builders:
        builder(LYRX_DIR / name)


def write_documentation() -> None:
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    readme = README_TEMPLATE.format(ACS_YEAR=2024)
    (PACKAGE_DIR / "README.md").write_text(readme, encoding="utf-8")
    (PACKAGE_DIR / "symbology.json").write_text(
        json.dumps(SYMBOLOGY_SPEC, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_archive() -> Path:
    if ARCHIVE_PATH.exists():
        ARCHIVE_PATH.unlink()
    with zipfile.ZipFile(ARCHIVE_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(PACKAGE_DIR.parent))
    return ARCHIVE_PATH


def main() -> None:
    copy_shapefiles()
    write_layer_files()
    write_documentation()
    archive = write_archive()
    print(f"Wrote ArcGIS Pro package to {PACKAGE_DIR}/")
    print(f"  ├─ data/      ({sum(1 for _ in DATA_DIR.rglob('*.shp'))} shapefiles)")
    print(f"  ├─ layers/    ({sum(1 for _ in LYRX_DIR.glob('*.lyrx'))} .lyrx files)")
    print(f"  ├─ README.md")
    print(f"  └─ symbology.json")
    print(f"Archive: {archive}")


if __name__ == "__main__":
    main()
