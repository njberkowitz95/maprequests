#!/usr/bin/env python3
"""Professional cartographic context map for Fremont, NE — Census Tract 9642.

This script downloads authoritative U.S. Census Bureau data
(TIGER/Line geometries + ACS 5-year B17001 poverty estimates),
performs the standard tabular join, and renders a publication-quality
map using a fixed page layout (title bar, map frame, locator inset,
legend, scale bar, north arrow, source line).

Outputs (default ``output/``):

* ``fremont_ne_ct9642_poverty_map.png`` — high-resolution raster.
* ``fremont_ne_ct9642_poverty_map.pdf`` — vector PDF (print ready).
* ``shapefiles/`` — per-layer ESRI shapefiles and a zip archive,
  ready for direct use in ArcGIS Pro / QGIS.
* ``arcgis_pro/`` — ArcGIS Pro deliverable: shapefiles + ``.lyrx``
  layer files + step-by-step README. Generated separately by
  ``scripts/build_arcgis_pro_package.py``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import pandas as pd
import requests
from matplotlib.cm import ScalarMappable
from matplotlib.gridspec import GridSpec
from matplotlib.patches import FancyArrowPatch
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from shapely.geometry import box


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

ACS_YEAR = 2024
STATE_FIPS = "31"
DODGE_COUNTY_FIPS = "053"
TARGET_TRACT = "964200"
TARGET_GEOID = f"{STATE_FIPS}{DODGE_COUNTY_FIPS}{TARGET_TRACT}"
FREMONT_PLACE_GEOID = "3117670"

# NAD83 / UTM zone 14N — projected, equal-area-friendly for eastern NE.
PROJECT_CRS = "EPSG:26914"

DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")
SHAPEFILE_SUBDIR = "shapefiles"

TIGER_URLS = {
    "tracts":      f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/TRACT/tl_{ACS_YEAR}_{STATE_FIPS}_tract.zip",
    "places":      f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/PLACE/tl_{ACS_YEAR}_{STATE_FIPS}_place.zip",
    "counties":    f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/COUNTY/tl_{ACS_YEAR}_us_county.zip",
    "states":      f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/STATE/tl_{ACS_YEAR}_us_state.zip",
    "roads":       f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/ROADS/tl_{ACS_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_roads.zip",
    "areawater":   f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/AREAWATER/tl_{ACS_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_areawater.zip",
    "linearwater": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/LINEARWATER/tl_{ACS_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_linearwater.zip",
}

# Restrained, neutral cartographic palette.
PALETTE = {
    "page":          "#ffffff",
    "land":          "#f4efe6",
    "tract_edge":    "#a89f8e",
    "county_edge":   "#5e574a",
    "city_edge":     "#1f4e6b",
    "focus_fill":    "#1f6feb",
    "focus_edge":    "#0a2540",
    "focus_glow":    "#08306b",
    "road_case":     "#ffffff",
    "highway":       "#5b524a",
    "arterial":      "#7a7164",
    "local":         "#cfc8ba",
    "rail":          "#3a3a3a",
    "water_fill":    "#aac9e0",
    "water_edge":    "#7fa7c4",
    "label_dark":    "#1d1d1d",
    "label_muted":   "#5d5648",
    "frame":         "#2b2b2b",
    "no_data":       "#ececec",
    "title_bar":     "#0a2540",
    "title_fg":      "#ffffff",
}

# Class breaks (%): rounded census-tract poverty thresholds commonly used
# in ACS reporting. ≥30 % is bundled into a single "high distress" class.
POVERTY_BREAKS  = [0, 5, 10, 15, 20, 30, 100]
POVERTY_LABELS  = ["< 5%", "5 – 10%", "10 – 15%", "15 – 20%", "20 – 30%", "≥ 30%"]
POVERTY_COLORS  = ["#fff7d6", "#fee08b", "#fdae61", "#f46d43", "#d73027", "#7a0177"]


# --------------------------------------------------------------------------- #
# Data acquisition                                                            #
# --------------------------------------------------------------------------- #

def fetch_acs_poverty(cache_path: Path) -> pd.DataFrame:
    """Download (or load cached) ACS 5-year poverty estimates for Dodge County."""
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
    else:
        url = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
        params = {
            "get": "NAME,B17001_001E,B17001_002E",
            "for": "tract:*",
            "in": f"state:{STATE_FIPS} county:{DODGE_COUNTY_FIPS}",
        }
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        raw = response.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw, indent=2))

    columns, *records = raw
    df = pd.DataFrame(records, columns=columns)
    df["GEOID"]     = df["state"] + df["county"] + df["tract"]
    df["pov_total"] = pd.to_numeric(df["B17001_001E"], errors="coerce")
    df["pov_count"] = pd.to_numeric(df["B17001_002E"], errors="coerce")
    df["pov_rate"]  = (df["pov_count"] / df["pov_total"]) * 100.0
    return df[["GEOID", "NAME", "pov_total", "pov_count", "pov_rate"]]


def read_tiger_layer(name: str) -> gpd.GeoDataFrame:
    return gpd.read_file(TIGER_URLS[name], engine="pyogrio")


# --------------------------------------------------------------------------- #
# Geometry helpers                                                            #
# --------------------------------------------------------------------------- #

def buffered_extent(
    bounds: Iterable[float], x_pad: float, y_pad: float,
) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    width, height = maxx - minx, maxy - miny
    return (minx - width * x_pad, miny - height * y_pad,
            maxx + width * x_pad, maxy + height * y_pad)


def aspect_corrected_extent(
    bounds: Iterable[float], target_ratio: float,
) -> tuple[float, float, float, float]:
    """Pad the smaller axis so the extent matches a target width/height ratio."""
    minx, miny, maxx, maxy = bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    width  = maxx - minx
    height = maxy - miny
    current = width / height if height else target_ratio
    if current < target_ratio:
        width = height * target_ratio
    else:
        height = width / target_ratio
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def classify_poverty(values: pd.Series) -> pd.Series:
    return pd.cut(values, bins=POVERTY_BREAKS, labels=POVERTY_LABELS,
                  include_lowest=True, right=False)


# --------------------------------------------------------------------------- #
# Map furniture                                                               #
# --------------------------------------------------------------------------- #

def add_scale_bar(ax: plt.Axes, length_miles: int = 2) -> None:
    """Two-segment alternating scale bar drawn in projected meters."""
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    length_m  = length_miles * 1609.344
    segment_m = length_m / 2
    start_x = x0 + (x1 - x0) * 0.04
    start_y = y0 + (y1 - y0) * 0.045
    height  = (y1 - y0) * 0.010

    for idx, fc in enumerate(["#1d1d1d", "#ffffff"]):
        ax.add_patch(mpatches.Rectangle(
            (start_x + idx * segment_m, start_y), segment_m, height,
            facecolor=fc, edgecolor="#1d1d1d", linewidth=0.8, zorder=30,
        ))

    txt_kwargs = dict(ha="center", va="bottom", fontsize=7.5,
                      color=PALETTE["label_dark"], zorder=31)
    ax.text(start_x,                    start_y + height * 1.4, "0", **txt_kwargs)
    ax.text(start_x + segment_m,        start_y + height * 1.4, f"{length_miles // 2}", **txt_kwargs)
    ax.text(start_x + length_m,         start_y + height * 1.4, f"{length_miles} mi", **txt_kwargs)


def add_north_arrow(ax: plt.Axes) -> None:
    """Compact north arrow drawn in axes coordinates."""
    arrow = FancyArrowPatch(
        (0.965, 0.86), (0.965, 0.955),
        transform=ax.transAxes, arrowstyle="-|>",
        mutation_scale=15, linewidth=1.4,
        color=PALETTE["frame"], zorder=40,
    )
    ax.add_patch(arrow)
    ax.text(0.965, 0.965, "N", transform=ax.transAxes,
            ha="center", va="bottom", fontsize=11, weight="bold",
            color=PALETTE["frame"], zorder=41)


def label_point(
    ax: plt.Axes, x: float, y: float, text: str, *,
    size: int = 10, weight: str = "normal",
    color: str = PALETTE["label_dark"],
    halo: bool = True, italic: bool = False, zorder: int = 25,
) -> None:
    txt = ax.text(
        x, y, text, ha="center", va="center",
        fontsize=size, weight=weight, color=color,
        style="italic" if italic else "normal", zorder=zorder,
    )
    if halo:
        txt.set_path_effects([
            path_effects.Stroke(linewidth=2.6, foreground="white"),
            path_effects.Normal(),
        ])


def draw_locator_inset(
    parent_ax: plt.Axes,
    states: gpd.GeoDataFrame,
    counties: gpd.GeoDataFrame,
    dodge: gpd.GeoDataFrame,
) -> None:
    """Inset map: outline of Nebraska with all counties and Dodge highlighted."""
    inset = inset_axes(
        parent_ax, width="22%", height="22%", loc="upper left",
        bbox_to_anchor=(0.012, -0.012, 1.0, 1.0),
        bbox_transform=parent_ax.transAxes, borderpad=0.6,
    )
    nebraska = states[states["STATEFP"] == STATE_FIPS].to_crs(PROJECT_CRS)
    ne_counties = counties[counties["STATEFP"] == STATE_FIPS].to_crs(PROJECT_CRS)

    nebraska.plot(ax=inset, color="#f4efe6", edgecolor=PALETTE["county_edge"],
                  linewidth=0.9, zorder=2)
    ne_counties.boundary.plot(ax=inset, color="#cfc8ba", linewidth=0.4, zorder=3)
    dodge.plot(ax=inset, color=PALETTE["focus_fill"], alpha=0.92,
               edgecolor=PALETTE["focus_edge"], linewidth=1.0, zorder=4)

    minx, miny, maxx, maxy = nebraska.total_bounds
    pad = 0.02 * max(maxx - minx, maxy - miny)
    inset.set_xlim(minx - pad, maxx + pad)
    inset.set_ylim(miny - pad, maxy + pad)
    inset.set_aspect("equal")
    inset.set_xticks([]); inset.set_yticks([])
    for spine in inset.spines.values():
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(0.7)
    inset.set_facecolor("#ffffff")
    inset.set_title("Nebraska", fontsize=7.5, weight="bold",
                    color=PALETTE["label_dark"], pad=2)


def style_main_axes(ax: plt.Axes) -> None:
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(1.0)


# --------------------------------------------------------------------------- #
# Shapefile export                                                            #
# --------------------------------------------------------------------------- #

def write_shapefiles(
    out_dir: Path,
    *,
    target: gpd.GeoDataFrame,
    tracts_view: gpd.GeoDataFrame,
    fremont: gpd.GeoDataFrame,
    dodge: gpd.GeoDataFrame,
    roads_view: gpd.GeoDataFrame,
    water_area: gpd.GeoDataFrame,
    water_lines: gpd.GeoDataFrame,
    extent_polygon: gpd.GeoDataFrame,
) -> Path:
    """Write each map layer to its own shapefile, then bundle into a zip."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    layers: dict[str, gpd.GeoDataFrame] = {
        "ct9642_focus_tract":      target,
        "neighborhood_tracts_acs": tracts_view,
        "fremont_place":           fremont,
        "dodge_county":            dodge,
        "roads_clipped":           roads_view,
        "water_area":              water_area,
        "water_linear":            water_lines,
        "map_extent":              extent_polygon,
    }

    for name, gdf in layers.items():
        if gdf is None or gdf.empty:
            continue
        layer_dir = out_dir / name
        layer_dir.mkdir(parents=True, exist_ok=True)
        # Shapefile DBF field names are limited to 10 characters.
        truncated = gdf.copy()
        rename: dict[str, str] = {}
        seen: set[str] = set()
        for col in truncated.columns:
            if col == "geometry":
                continue
            short = col[:10]
            base, i = short, 1
            while short in seen:
                short = f"{base[:8]}{i:02d}"
                i += 1
            seen.add(short)
            if short != col:
                rename[col] = short
        if rename:
            truncated = truncated.rename(columns=rename)
        truncated.to_file(layer_dir / f"{name}.shp",
                          driver="ESRI Shapefile", engine="pyogrio")

    archive = out_dir.parent / "fremont_ne_ct9642_layers.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_dir.parent))
    return archive


# --------------------------------------------------------------------------- #
# Map composition                                                             #
# --------------------------------------------------------------------------- #

def build_map(
    output_dir: Path, *,
    basename: str = "fremont_ne_ct9642_poverty_map",
    write_shp: bool = True,
) -> dict[str, Path]:
    DATA_DIR.mkdir(exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ----- Acquire all source layers --------------------------------------- #
    acs       = fetch_acs_poverty(DATA_DIR / f"acs_{ACS_YEAR}_dodge_county_poverty.json")
    tracts    = read_tiger_layer("tracts")
    places    = read_tiger_layer("places")
    counties  = read_tiger_layer("counties")
    states    = read_tiger_layer("states")
    roads     = read_tiger_layer("roads")
    areawater = read_tiger_layer("areawater")
    linwater  = read_tiger_layer("linearwater")

    # ----- Filter / project to working CRS --------------------------------- #
    dodge   = counties[(counties["STATEFP"] == STATE_FIPS) &
                       (counties["COUNTYFP"] == DODGE_COUNTY_FIPS)].to_crs(PROJECT_CRS)
    fremont = places[places["GEOID"] == FREMONT_PLACE_GEOID].to_crs(PROJECT_CRS)
    dodge_tracts = tracts[(tracts["STATEFP"] == STATE_FIPS) &
                          (tracts["COUNTYFP"] == DODGE_COUNTY_FIPS)]
    dodge_tracts = dodge_tracts.merge(acs, on="GEOID", how="left").to_crs(PROJECT_CRS)
    roads     = roads.to_crs(PROJECT_CRS)
    areawater = areawater.to_crs(PROJECT_CRS)
    linwater  = linwater.to_crs(PROJECT_CRS)

    target = dodge_tracts[dodge_tracts["GEOID"] == TARGET_GEOID]
    if target.empty:
        raise RuntimeError(f"Target Census Tract GEOID {TARGET_GEOID} not found.")

    # ----- Compute the map extent ------------------------------------------ #
    # Frame the full City of Fremont with comfortable breathing room and
    # snap the aspect ratio to the map frame so the page composes cleanly.
    base_bounds = fremont.total_bounds.copy()
    base_bounds[0] = min(base_bounds[0], target.total_bounds[0])
    base_bounds[1] = min(base_bounds[1], target.total_bounds[1])
    base_bounds[2] = max(base_bounds[2], target.total_bounds[2])
    base_bounds[3] = max(base_bounds[3], target.total_bounds[3])
    padded = buffered_extent(base_bounds, x_pad=0.20, y_pad=0.20)
    extent = aspect_corrected_extent(padded, target_ratio=10.0 / 7.5)

    extent_geom = box(*extent)
    extent_polygon = gpd.GeoDataFrame(
        {"name": ["map_extent"]}, geometry=[extent_geom], crs=PROJECT_CRS,
    )

    tracts_view = dodge_tracts[dodge_tracts.intersects(extent_geom)].copy()
    tracts_view["pov_class"] = classify_poverty(tracts_view["pov_rate"])
    roads_view = roads[roads.intersects(extent_geom)].copy()
    water_area_view  = areawater[areawater.intersects(extent_geom)].copy()
    water_lines_view = linwater[linwater.intersects(extent_geom)].copy()

    # Drop tiny noisy water polygons (retention ponds, etc.) for clarity.
    water_area_view = water_area_view[water_area_view.geometry.area > 8000]

    # ----- Color ramp & matplotlib defaults -------------------------------- #
    cmap = mcolors.ListedColormap(POVERTY_COLORS)
    cmap.set_bad(PALETTE["no_data"])
    norm = mcolors.BoundaryNorm(POVERTY_BREAKS, cmap.N)

    plt.rcParams.update({
        "font.family":         "DejaVu Sans",
        "axes.titlesize":      11,
        "axes.titleweight":    "bold",
        "axes.edgecolor":      PALETTE["frame"],
        "savefig.facecolor":   PALETTE["page"],
    })

    # ----- Page layout ------------------------------------------------------ #
    fig = plt.figure(figsize=(13, 9.5), dpi=200, facecolor=PALETTE["page"])
    gs = GridSpec(
        nrows=3, ncols=2,
        height_ratios=[0.10, 0.82, 0.08],
        width_ratios=[0.74, 0.26],
        hspace=0.04, wspace=0.025,
        left=0.025, right=0.985, top=0.985, bottom=0.015,
    )

    title_ax = fig.add_subplot(gs[0, :])
    map_ax   = fig.add_subplot(gs[1, 0])
    side_ax  = fig.add_subplot(gs[1, 1])
    foot_ax  = fig.add_subplot(gs[2, :])

    # ----- Title bar -------------------------------------------------------- #
    title_ax.set_facecolor(PALETTE["title_bar"])
    title_ax.set_xticks([]); title_ax.set_yticks([])
    for spine in title_ax.spines.values():
        spine.set_visible(False)
    title_ax.text(
        0.012, 0.66,
        "FREMONT, NEBRASKA",
        transform=title_ax.transAxes,
        fontsize=20, weight="bold", color=PALETTE["title_fg"],
        ha="left", va="center",
    )
    title_ax.text(
        0.012, 0.22,
        "Census Tract 9642  ·  Cartographic Context & Poverty Overview",
        transform=title_ax.transAxes,
        fontsize=11, color="#dbe5ee",
        ha="left", va="center",
    )
    target_rate = float(target["pov_rate"].iloc[0])
    target_pop  = int(target["pov_total"].iloc[0])
    target_pov  = int(target["pov_count"].iloc[0])
    title_ax.text(
        0.988, 0.50,
        f"CT 9642 poverty rate: {target_rate:.1f} %"
        f"   ·   {target_pov:,} of {target_pop:,} persons"
        f"   ·   ACS {ACS_YEAR} 5-yr",
        transform=title_ax.transAxes,
        fontsize=10, color=PALETTE["title_fg"],
        ha="right", va="center",
    )

    # ----- Map frame -------------------------------------------------------- #
    map_ax.set_facecolor(PALETTE["land"])
    map_ax.set_xlim(extent[0], extent[2])
    map_ax.set_ylim(extent[1], extent[3])

    # County context (subtle).
    dodge.boundary.plot(ax=map_ax, color=PALETTE["county_edge"],
                        linewidth=1.3, zorder=2)

    # Tract poverty choropleth.
    tracts_view.plot(
        ax=map_ax, column="pov_rate", cmap=cmap, norm=norm,
        alpha=0.62, edgecolor=PALETTE["tract_edge"], linewidth=0.55,
        zorder=3,
        missing_kwds={"color": PALETTE["no_data"], "edgecolor": "#bdbdbd",
                      "hatch": "///"},
    )

    # Hydrography (drawn beneath roads but above the choropleth).
    if not water_area_view.empty:
        water_area_view.plot(
            ax=map_ax, color=PALETTE["water_fill"],
            edgecolor=PALETTE["water_edge"], linewidth=0.4,
            alpha=0.9, zorder=4,
        )
    if not water_lines_view.empty:
        water_lines_view.plot(
            ax=map_ax, color=PALETTE["water_edge"], linewidth=0.5,
            alpha=0.8, zorder=4,
        )

    # Road hierarchy (cased symbology).
    highways  = roads_view[roads_view["MTFCC"] == "S1100"]
    arterials = roads_view[roads_view["MTFCC"].isin(["S1200", "S1630"])]
    locals_   = roads_view[roads_view["MTFCC"] == "S1400"]
    locals_.plot(ax=map_ax, color=PALETTE["local"],
                 linewidth=0.45, alpha=0.85, zorder=5)
    arterials.plot(ax=map_ax, color=PALETTE["road_case"],
                   linewidth=2.6, zorder=6)
    arterials.plot(ax=map_ax, color=PALETTE["arterial"],
                   linewidth=1.1, zorder=7)
    highways.plot(ax=map_ax, color=PALETTE["road_case"],
                  linewidth=3.4, zorder=8)
    highways.plot(ax=map_ax, color=PALETTE["highway"],
                  linewidth=1.6, zorder=9)

    # City boundary.
    fremont.boundary.plot(ax=map_ax, color=PALETTE["city_edge"],
                          linewidth=1.7, linestyle=(0, (5, 3)), zorder=10)

    # Focus tract: subtle glow + strong outline.
    target_glow = target.copy()
    target_glow["geometry"] = target.buffer(120)
    target_glow.plot(ax=map_ax, color=PALETTE["focus_glow"],
                     alpha=0.10, zorder=11)
    target.plot(ax=map_ax, color=PALETTE["focus_fill"], alpha=0.42,
                edgecolor=PALETTE["focus_edge"], linewidth=2.4, zorder=12)
    target.boundary.plot(ax=map_ax, color=PALETTE["focus_edge"],
                         linewidth=2.6, zorder=13)

    # Anchor labels. Place the city label well above the focus tract so it
    # never collides with the tract label or its outline.
    target_geom_obj = target.geometry.iloc[0]
    target_pt = target_geom_obj.representative_point()
    extent_h = extent[3] - extent[1]
    city_label_y = min(
        extent[3] - extent_h * 0.06,
        target_geom_obj.bounds[3] + extent_h * 0.18,
    )
    label_point(map_ax, target_pt.x, city_label_y,
                "City of Fremont", size=13, weight="bold",
                color=PALETTE["city_edge"])

    label_point(map_ax, target_pt.x, target_pt.y,
                "Census Tract 9642", size=10.5, weight="bold",
                color=PALETTE["focus_edge"])

    label_point(
        map_ax,
        extent[0] + (extent[2] - extent[0]) * 0.86,
        extent[1] + (extent[3] - extent[1]) * 0.07,
        "Dodge County", size=11, italic=True, color=PALETTE["label_muted"],
    )

    # Major-road labels (clipped + de-duplicated).
    inset_clip = box(extent[0], extent[1] + (extent[3] - extent[1]) * 0.78,
                     extent[0] + (extent[2] - extent[0]) * 0.30, extent[3])
    seen_names: set[str] = set()
    label_targets = pd.concat([highways, arterials], ignore_index=True)
    for _, row in label_targets.dropna(subset=["FULLNAME"]).iterrows():
        name = row["FULLNAME"]
        if name in seen_names:
            continue
        if not any(token in name for token in
                   ("US Hwy", "State Hwy", "Broad St", "Main St", "Military",
                    "Bell St", "23rd")):
            continue
        clipped = row.geometry.intersection(extent_geom).difference(inset_clip)
        if clipped.is_empty:
            continue
        if clipped.geom_type == "MultiLineString":
            seg = max(list(clipped.geoms), key=lambda g: g.length)
        else:
            seg = clipped
        if seg.length < 800:
            continue
        seen_names.add(name)
        pt = seg.interpolate(0.5, normalized=True)
        if pt.is_empty:
            continue
        label_point(map_ax, pt.x, pt.y, name, size=7,
                    color="#3a3a3a", halo=True, zorder=15)

    style_main_axes(map_ax)
    add_scale_bar(map_ax, length_miles=2)
    add_north_arrow(map_ax)
    draw_locator_inset(map_ax, states, counties, dodge)

    # ----- Side panel: legend + colorbar ----------------------------------- #
    side_ax.set_facecolor(PALETTE["page"])
    side_ax.set_xticks([]); side_ax.set_yticks([])
    for spine in side_ax.spines.values():
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(1.0)
    side_ax.set_xlim(0, 1); side_ax.set_ylim(0, 1)

    # Header bar
    side_ax.text(
        0.5, 0.975, "LEGEND",
        ha="center", va="top",
        fontsize=11, weight="bold", color=PALETTE["label_dark"],
        transform=side_ax.transAxes,
    )
    side_ax.plot([0.06, 0.94], [0.948, 0.948], color=PALETTE["frame"],
                 linewidth=0.6, transform=side_ax.transAxes)

    # ---- Section 1: choropleth swatches (the headline variable) ----------- #
    side_ax.text(
        0.06, 0.915,
        f"ACS {ACS_YEAR} 5-year poverty rate",
        ha="left", va="top",
        fontsize=9.6, weight="bold", color=PALETTE["label_dark"],
        transform=side_ax.transAxes,
    )
    side_ax.text(
        0.06, 0.885,
        "by census tract  (B17001)",
        ha="left", va="top",
        fontsize=8.4, color=PALETTE["label_muted"],
        transform=side_ax.transAxes,
    )

    swatch_x = 0.08
    swatch_w = 0.10
    swatch_h = 0.033
    swatch_top = 0.840
    for i, (label, color) in enumerate(zip(POVERTY_LABELS, POVERTY_COLORS)):
        y = swatch_top - i * (swatch_h + 0.011)
        side_ax.add_patch(mpatches.Rectangle(
            (swatch_x, y), swatch_w, swatch_h,
            facecolor=color, edgecolor=PALETTE["frame"],
            linewidth=0.5, transform=side_ax.transAxes,
        ))
        side_ax.text(
            swatch_x + swatch_w + 0.04, y + swatch_h / 2,
            label, ha="left", va="center",
            fontsize=9, color=PALETTE["label_dark"],
            transform=side_ax.transAxes,
        )

    # No-data swatch directly underneath the ramp.
    nd_y = swatch_top - len(POVERTY_LABELS) * (swatch_h + 0.011) - 0.01
    side_ax.add_patch(mpatches.Rectangle(
        (swatch_x, nd_y), swatch_w, swatch_h,
        facecolor=PALETTE["no_data"], edgecolor="#bdbdbd",
        linewidth=0.5, hatch="///", transform=side_ax.transAxes,
    ))
    side_ax.text(
        swatch_x + swatch_w + 0.04, nd_y + swatch_h / 2,
        "No ACS data", ha="left", va="center",
        fontsize=9, color=PALETTE["label_dark"],
        transform=side_ax.transAxes,
    )

    # Section divider.
    divider_y = nd_y - 0.04
    side_ax.plot([0.06, 0.94], [divider_y, divider_y],
                 color=PALETTE["frame"], linewidth=0.4,
                 transform=side_ax.transAxes)

    # ---- Section 2: map layers ------------------------------------------- #
    side_ax.text(
        0.06, divider_y - 0.025,
        "Map layers",
        ha="left", va="top",
        fontsize=9.6, weight="bold", color=PALETTE["label_dark"],
        transform=side_ax.transAxes,
    )

    legend_handles = [
        mpatches.Patch(facecolor=PALETTE["focus_fill"],
                       edgecolor=PALETTE["focus_edge"],
                       linewidth=2.0, alpha=0.55,
                       label="Focus area — CT 9642"),
        mlines.Line2D([], [], color=PALETTE["city_edge"],
                      linestyle=(0, (5, 3)), linewidth=1.7,
                      label="City of Fremont"),
        mlines.Line2D([], [], color=PALETTE["county_edge"],
                      linewidth=1.3, label="Dodge County"),
        mlines.Line2D([], [], color=PALETTE["highway"],
                      linewidth=2.0, label="U.S. & state highway"),
        mlines.Line2D([], [], color=PALETTE["arterial"],
                      linewidth=1.4, label="Arterial / business"),
        mlines.Line2D([], [], color=PALETTE["local"],
                      linewidth=1.0, label="Local road"),
        mpatches.Patch(facecolor=PALETTE["water_fill"],
                       edgecolor=PALETTE["water_edge"],
                       linewidth=0.6, label="Lakes & rivers"),
    ]
    map_layers_legend = side_ax.legend(
        handles=legend_handles,
        loc="upper left",
        bbox_to_anchor=(0.06, divider_y - 0.06),
        frameon=False,
        fontsize=8.6,
        labelspacing=0.55,
        handlelength=2.2,
        handletextpad=0.7,
        alignment="left",
        borderpad=0.0,
    )
    side_ax.add_artist(map_layers_legend)

    # Footnote at the very bottom of the panel.
    side_ax.text(
        0.06, 0.012,
        "ACS B17001 — population for whom\npoverty status is determined.",
        ha="left", va="bottom",
        fontsize=7.4, color=PALETTE["label_muted"],
        transform=side_ax.transAxes,
    )

    # ----- Footer ----------------------------------------------------------- #
    foot_ax.set_xticks([]); foot_ax.set_yticks([])
    for spine in foot_ax.spines.values():
        spine.set_visible(False)
    foot_ax.set_facecolor(PALETTE["page"])
    foot_ax.set_xlim(0, 1); foot_ax.set_ylim(0, 1)

    foot_ax.text(
        0.005, 0.78,
        "Projection: NAD83 / UTM Zone 14N (EPSG:26914)  ·  Units: meters",
        fontsize=7.6, color=PALETTE["label_muted"],
        ha="left", va="center", transform=foot_ax.transAxes,
    )
    foot_ax.text(
        0.995, 0.78,
        "Prepared with open data",
        fontsize=7.6, color=PALETTE["label_muted"],
        ha="right", va="center", transform=foot_ax.transAxes,
    )
    foot_ax.text(
        0.005, 0.25,
        f"Sources: U.S. Census Bureau TIGER/Line {ACS_YEAR} "
        "(tracts, places, counties, states, roads, hydrography); "
        f"American Community Survey {ACS_YEAR} 5-Year Estimates, table B17001.",
        fontsize=7.6, color=PALETTE["label_muted"],
        ha="left", va="center", transform=foot_ax.transAxes,
    )

    # ----- Render ----------------------------------------------------------- #
    png_path = output_dir / f"{basename}.png"
    pdf_path = output_dir / f"{basename}.pdf"
    fig.savefig(png_path, facecolor=PALETTE["page"])
    fig.savefig(pdf_path, facecolor=PALETTE["page"])
    plt.close(fig)

    written: dict[str, Path] = {"png": png_path, "pdf": pdf_path}
    print(f"Wrote {png_path}")
    print(f"Wrote {pdf_path}")

    if write_shp:
        shp_dir = output_dir / SHAPEFILE_SUBDIR
        archive = write_shapefiles(
            shp_dir,
            target=target,
            tracts_view=tracts_view.drop(columns=["pov_class"], errors="ignore"),
            fremont=fremont,
            dodge=dodge,
            roads_view=roads_view,
            water_area=water_area_view,
            water_lines=water_lines_view,
            extent_polygon=extent_polygon,
        )
        written["shapefiles_dir"] = shp_dir
        written["shapefiles_zip"] = archive
        print(f"Wrote shapefiles to {shp_dir}/")
        print(f"Wrote {archive}")

    return written


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR,
                        help="Output directory (defaults to ./output).")
    parser.add_argument("--basename", type=str,
                        default="fremont_ne_ct9642_poverty_map",
                        help="Filename stem for the PNG and PDF.")
    parser.add_argument("--no-shapefiles", action="store_true",
                        help="Skip writing shapefile exports.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_map(args.output_dir, basename=args.basename,
              write_shp=not args.no_shapefiles)
