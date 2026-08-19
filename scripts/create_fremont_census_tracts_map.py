#!/usr/bin/env python3
"""Reference map of 2020 U.S. Census tracts within Fremont, Nebraska.

Downloads authoritative Census TIGER/Line geometries, selects every tract
that intersects the incorporated place (Fremont city, GEOID 3117670), clips
fills to city limits, and renders a print-ready vector PDF.

Outputs (default ``output/``):

* ``fremont_ne_census_tracts.pdf`` — vector PDF (primary deliverable)
* ``fremont_ne_census_tracts.png`` — raster preview
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.font_manager as fm
import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import pandas as pd
import requests
from matplotlib.patches import Polygon
from shapely.geometry import LineString, box

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

TIGER_YEAR = 2024
STATE_FIPS = "31"
DODGE_COUNTY_FIPS = "053"
FREMONT_PLACE_GEOID = "3117670"

# NAD83 / Nebraska (State Plane, meters) — local GIS standard.
PROJECT_CRS = "EPSG:32104"
SQM_PER_SQMI = 2_589_988.110336
METERS_PER_MILE = 1609.344

DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")
USER_AGENT = "FremontNE-CensusTractMap/1.0 (cartographic reference map)"

TIGER_URLS = {
    "tracts": (
        f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/TRACT/"
        f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract.zip"
    ),
    "places": (
        f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/PLACE/"
        f"tl_{TIGER_YEAR}_{STATE_FIPS}_place.zip"
    ),
    "roads": (
        f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/ROADS/"
        f"tl_{TIGER_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_roads.zip"
    ),
    "areawater": (
        f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/AREAWATER/"
        f"tl_{TIGER_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_areawater.zip"
    ),
    "linearwater": (
        f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/LINEARWATER/"
        f"tl_{TIGER_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_linearwater.zip"
    ),
}

# Simplified cartographic boundaries for the locator inset.
CB_STATE_URLS = [
    f"https://www2.census.gov/geo/tiger/GENZ{TIGER_YEAR}/shp/cb_{TIGER_YEAR}_us_state_20m.zip",
    "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_20m.zip",
]
CB_COUNTY_URLS = [
    f"https://www2.census.gov/geo/tiger/GENZ{TIGER_YEAR}/shp/cb_{TIGER_YEAR}_us_county_20m.zip",
    "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_20m.zip",
]

# Colorblind-safe qualitative fills (muted Okabe–Ito), assigned so
# neighboring tracts do not share a similar hue.
TRACT_COLORS = {
    "9638": "#7EC8E3",  # sky — northwest
    "9639": "#E8B86D",  # gold — west
    "9640": "#C989B8",  # mauve — center
    "9641": "#6FBF9A",  # green — east
    "9642": "#6A9BC7",  # blue — south-center
    "9643": "#E07A5F",  # coral — southeast
    "9644": "#C4B056",  # olive-gold — southwest
}

PALETTE = {
    "page": "#f7f4ee",
    "panel": "#fffdf8",
    "land": "#ebe4d6",
    "outside": "#d9d2c3",
    "tract_edge": "#3d3a32",
    "tract_edge_out": "#9a9284",
    "city_edge": "#1a365d",
    "road_case": "#ffffff",
    "highway": "#4a453c",
    "arterial": "#6e675c",
    "local": "#c9c1b2",
    "water_fill": "#9ec5dc",
    "water_edge": "#6a9bb8",
    "water_line": "#5b8eaa",
    "label_dark": "#1c1917",
    "label_muted": "#5c564c",
    "frame": "#1c1917",
    "title_bar": "#1a365d",
    "title_fg": "#f7f4ee",
    "rule": "#c4bba8",
}

ROAD_LABELS = {
    "US Hwy 30": "US 30",
    "US Hwy 77": "US 77",
    "US Hwy 275": "US 275",
    "US Hwy 30 Bus": "US 30 Bus",
}


# --------------------------------------------------------------------------- #
# Typography                                                                  #
# --------------------------------------------------------------------------- #

def configure_fonts() -> str:
    available = {f.name for f in fm.fontManager.ttflist}
    for family in ("Source Sans 3", "Public Sans", "Inter", "Liberation Sans", "DejaVu Sans"):
        if family in available:
            plt.rcParams.update({
                "font.family": family,
                "pdf.fonttype": 42,
                "ps.fonttype": 42,
                "svg.fonttype": "none",
                "axes.unicode_minus": False,
            })
            return family
    return "DejaVu Sans"


# --------------------------------------------------------------------------- #
# Data acquisition                                                            #
# --------------------------------------------------------------------------- #

def download_file(url: str, dest: Path, timeout: int = 120) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    headers = {"User-Agent": USER_AGENT}
    with requests.get(url, headers=headers, timeout=timeout, stream=True) as response:
        response.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)
        tmp.replace(dest)
    return dest


def read_shapefile_zip(path: Path) -> gpd.GeoDataFrame:
    return gpd.read_file(path, engine="pyogrio")


def fetch_first_available(urls: list[str], dest_dir: Path, stem: str) -> gpd.GeoDataFrame:
    last_error: Exception | None = None
    for url in urls:
        filename = url.rsplit("/", 1)[-1]
        dest = dest_dir / filename
        try:
            download_file(url, dest)
            return read_shapefile_zip(dest)
        except Exception as exc:  # noqa: BLE001 — try the next vintage
            last_error = exc
            continue
    raise RuntimeError(f"Unable to download {stem}: {last_error}") from last_error


def load_layers(cache_dir: Path) -> dict[str, gpd.GeoDataFrame]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    layers: dict[str, gpd.GeoDataFrame] = {}
    for name, url in TIGER_URLS.items():
        dest = cache_dir / url.rsplit("/", 1)[-1]
        download_file(url, dest)
        layers[name] = read_shapefile_zip(dest)
    layers["states"] = fetch_first_available(CB_STATE_URLS, cache_dir, "states")
    layers["counties"] = fetch_first_available(CB_COUNTY_URLS, cache_dir, "counties")
    return layers


# --------------------------------------------------------------------------- #
# Geometry helpers                                                            #
# --------------------------------------------------------------------------- #

def aspect_corrected_extent(
    bounds: tuple[float, float, float, float],
    target_ratio: float,
    pad: float = 0.08,
) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    width, height = (maxx - minx) * (1 + pad * 2), (maxy - miny) * (1 + pad * 2)
    current = width / height if height else target_ratio
    if current < target_ratio:
        width = height * target_ratio
    else:
        height = width / target_ratio
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def longest_segment(geom) -> LineString | None:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "LineString":
        return geom
    if geom.geom_type == "MultiLineString":
        parts = list(geom.geoms)
        return max(parts, key=lambda g: g.length) if parts else None
    if geom.geom_type == "GeometryCollection":
        lines = [g for g in geom.geoms if g.geom_type in ("LineString", "MultiLineString")]
        if not lines:
            return None
        merged = lines[0]
        for extra in lines[1:]:
            merged = merged.union(extra)
        return longest_segment(merged)
    return None


def clip_to_extent(gdf: gpd.GeoDataFrame, extent_geom) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    hit = gdf[gdf.intersects(extent_geom)].copy()
    if hit.empty:
        return hit
    hit["geometry"] = hit.geometry.intersection(extent_geom)
    hit = hit[~hit.geometry.is_empty]
    return hit


# --------------------------------------------------------------------------- #
# Map furniture                                                               #
# --------------------------------------------------------------------------- #

def halo(size: float = 2.8, color: str = "white") -> list:
    return [path_effects.Stroke(linewidth=size, foreground=color), path_effects.Normal()]


def add_scale_bar(ax: plt.Axes, length_miles: float = 1.0) -> None:
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    length_m = length_miles * METERS_PER_MILE
    n_seg = 2
    segment_m = length_m / n_seg
    start_x = x0 + (x1 - x0) * 0.035
    start_y = y0 + (y1 - y0) * 0.045
    height = (y1 - y0) * 0.012
    colors = ["#1c1917", "#ffffff"]

    background = mpatches.FancyBboxPatch(
        (start_x - (x1 - x0) * 0.012, start_y - height * 1.6),
        length_m + (x1 - x0) * 0.055,
        height * 5.2,
        boxstyle="round,pad=0,rounding_size=0",
        facecolor="#f7f4ee",
        edgecolor="none",
        alpha=0.82,
        zorder=29,
    )
    ax.add_patch(background)

    for idx in range(n_seg):
        ax.add_patch(mpatches.Rectangle(
            (start_x + idx * segment_m, start_y),
            segment_m,
            height,
            facecolor=colors[idx % 2],
            edgecolor="#1c1917",
            linewidth=0.7,
            zorder=30,
        ))

    labels = ["0", f"{length_miles / 2:g}", f"{length_miles:g} mi"]
    positions = [start_x, start_x + segment_m, start_x + length_m]
    for x, text in zip(positions, labels):
        ax.text(
            x, start_y + height * 1.55, text,
            ha="center", va="bottom", fontsize=7.5,
            color=PALETTE["label_dark"], zorder=31,
        )


def add_north_arrow(ax: plt.Axes) -> None:
    """Grid-north arrow in axes coordinates (State Plane y-axis)."""
    cx, cy = 0.955, 0.86
    shaft = mpatches.FancyBboxPatch(
        (cx - 0.006, cy),
        0.012,
        0.055,
        boxstyle="square,pad=0",
        facecolor=PALETTE["frame"],
        edgecolor="none",
        transform=ax.transAxes,
        zorder=40,
    )
    ax.add_patch(shaft)
    head = Polygon(
        [(cx - 0.018, cy + 0.055), (cx + 0.018, cy + 0.055), (cx, cy + 0.105)],
        closed=True,
        facecolor=PALETTE["frame"],
        edgecolor="none",
        transform=ax.transAxes,
        zorder=41,
    )
    ax.add_patch(head)
    ax.text(
        cx, cy + 0.112, "N",
        transform=ax.transAxes, ha="center", va="bottom",
        fontsize=10, weight="bold", color=PALETTE["frame"], zorder=42,
    )


def label_point(
    ax: plt.Axes,
    x: float,
    y: float,
    text: str,
    *,
    size: float = 10,
    weight: str = "bold",
    color: str = PALETTE["label_dark"],
    zorder: int = 26,
) -> None:
    txt = ax.text(
        x, y, text, ha="center", va="center",
        fontsize=size, weight=weight, color=color, zorder=zorder,
    )
    txt.set_path_effects(halo(3.2))


def draw_locator(
    ax: plt.Axes,
    states: gpd.GeoDataFrame,
    counties: gpd.GeoDataFrame,
    fremont_ll: gpd.GeoDataFrame,
) -> None:
    nebraska = states[states["STATEFP"] == STATE_FIPS]
    ne_counties = counties[counties["STATEFP"] == STATE_FIPS]
    dodge = ne_counties[ne_counties["COUNTYFP"] == DODGE_COUNTY_FIPS]

    nebraska.plot(ax=ax, color="#f3eee4", edgecolor=PALETTE["city_edge"], linewidth=0.9, zorder=2)
    ne_counties.boundary.plot(ax=ax, color="#c5bba8", linewidth=0.25, zorder=3)
    dodge.plot(ax=ax, color="#1a365d", edgecolor="#0f2440", linewidth=0.4, zorder=4)

    pt = fremont_ll.geometry.union_all().centroid
    ax.scatter([pt.x], [pt.y], s=14, c="#e07a5f", edgecolors="white", linewidths=0.6, zorder=5)

    minx, miny, maxx, maxy = nebraska.total_bounds
    pad_x, pad_y = (maxx - minx) * 0.04, (maxy - miny) * 0.06
    ax.set_xlim(minx - pad_x, maxx + pad_x)
    ax.set_ylim(miny - pad_y, maxy + pad_y)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(0.6)
    ax.set_title("Nebraska", fontsize=8, color=PALETTE["label_dark"], pad=3, loc="left")


# --------------------------------------------------------------------------- #
# Map construction                                                            #
# --------------------------------------------------------------------------- #

def prepare_data(layers: dict[str, gpd.GeoDataFrame]) -> dict:
    places = layers["places"].to_crs(PROJECT_CRS)
    tracts = layers["tracts"].to_crs(PROJECT_CRS)
    roads = layers["roads"].to_crs(PROJECT_CRS)
    water_area = layers["areawater"].to_crs(PROJECT_CRS)
    water_line = layers["linearwater"].to_crs(PROJECT_CRS)

    fremont = places.loc[places["GEOID"] == FREMONT_PLACE_GEOID].copy()
    if fremont.empty:
        raise RuntimeError(f"Fremont city (GEOID {FREMONT_PLACE_GEOID}) not found in TIGER places.")
    city = fremont.geometry.union_all()

    dodge_tracts = tracts.loc[tracts["COUNTYFP"] == DODGE_COUNTY_FIPS].copy()
    intersecting = dodge_tracts.loc[dodge_tracts.intersects(city)].copy()
    intersecting["tract"] = intersecting["NAME"].astype(str)
    intersecting["fill"] = intersecting["tract"].map(TRACT_COLORS)

    missing = intersecting.loc[intersecting["fill"].isna(), "tract"].tolist()
    if missing:
        extras = ["#8d99ae", "#adb5bd", "#6c757d", "#495057"]
        for tract_id, color in zip(missing, extras):
            TRACT_COLORS[tract_id] = color
        intersecting["fill"] = intersecting["tract"].map(TRACT_COLORS)

    clipped = intersecting.copy()
    original_area = intersecting.geometry.area
    clipped["geometry"] = clipped.geometry.intersection(city)
    clipped = clipped.loc[~clipped.geometry.is_empty].copy()
    clipped["area_sqmi"] = clipped.geometry.area / SQM_PER_SQMI
    clipped["overlap_pct"] = clipped.geometry.area / original_area.loc[clipped.index] * 100.0
    clipped = clipped.sort_values("tract")

    city_area_sqmi = city.area / SQM_PER_SQMI
    return {
        "fremont": fremont,
        "city": city,
        "intersecting": intersecting,
        "clipped": clipped,
        "roads": roads,
        "water_area": water_area,
        "water_line": water_line,
        "states": layers["states"],
        "counties": layers["counties"],
        "city_area_sqmi": city_area_sqmi,
    }


def style_map_frame(ax: plt.Axes) -> None:
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor(PALETTE["land"])
    for spine in ax.spines.values():
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(1.15)


def draw_main_map(ax: plt.Axes, data: dict, extent: tuple[float, float, float, float]) -> None:
    extent_geom = box(*extent)
    city = data["city"]
    clipped = data["clipped"]
    intersecting = data["intersecting"]

    ax.set_xlim(extent[0], extent[2])
    ax.set_ylim(extent[1], extent[3])
    ax.set_aspect("equal")
    style_map_frame(ax)

    # Land outside the city.
    outside = extent_geom.difference(city)
    if not outside.is_empty:
        gpd.GeoSeries([outside], crs=PROJECT_CRS).plot(
            ax=ax, color=PALETTE["outside"], edgecolor="none", zorder=1,
        )

    # Full tract lines that continue beyond city limits (context).
    outside_tracts = intersecting.copy()
    outside_tracts["geometry"] = outside_tracts.geometry.difference(city)
    outside_tracts = outside_tracts.loc[~outside_tracts.geometry.is_empty]
    if not outside_tracts.empty:
        clip_to_extent(outside_tracts, extent_geom).boundary.plot(
            ax=ax, color=PALETTE["tract_edge_out"], linewidth=0.7,
            linestyle=(0, (4, 2.5)), zorder=3,
        )

    # Tract fills inside the city.
    for _, row in clipped.iterrows():
        gpd.GeoSeries([row.geometry], crs=PROJECT_CRS).plot(
            ax=ax, color=row["fill"], edgecolor="none", zorder=4,
        )

    # Hydrography.
    water_area = clip_to_extent(data["water_area"], extent_geom)
    if not water_area.empty:
        water_area.plot(
            ax=ax, color=PALETTE["water_fill"], edgecolor=PALETTE["water_edge"],
            linewidth=0.35, zorder=5,
        )
    water_line = clip_to_extent(data["water_line"], extent_geom)
    if not water_line.empty:
        major = water_line[water_line["FULLNAME"].fillna("").str.contains("Crk|Creek|River", case=False)]
        draw_lines = major if not major.empty else water_line
        draw_lines.plot(ax=ax, color=PALETTE["water_line"], linewidth=0.9, zorder=6)

    # Roads.
    roads = clip_to_extent(data["roads"], extent_geom)
    local = roads[roads["MTFCC"] == "S1400"]
    arterial = roads[roads["MTFCC"] == "S1200"]
    highway = roads[roads["MTFCC"].isin(["S1100", "S1630"])]

    if not local.empty:
        in_city = local[local.intersects(city)]
        if not in_city.empty:
            in_city.plot(ax=ax, color=PALETTE["local"], linewidth=0.28, zorder=7)
    if not arterial.empty:
        arterial.plot(ax=ax, color=PALETTE["road_case"], linewidth=1.55, zorder=8, solid_capstyle="round")
        arterial.plot(ax=ax, color=PALETTE["arterial"], linewidth=0.7, zorder=9, solid_capstyle="round")
    if not highway.empty:
        highway.plot(ax=ax, color=PALETTE["road_case"], linewidth=2.15, zorder=10, solid_capstyle="round")
        highway.plot(ax=ax, color=PALETTE["highway"], linewidth=1.05, zorder=11, solid_capstyle="round")

    # Tract boundaries inside the city (on top of roads so units stay clear).
    clipped.boundary.plot(ax=ax, color=PALETTE["tract_edge"], linewidth=1.05, zorder=12)

    # City limit.
    fremont_outline = data["fremont"].boundary
    fremont_outline.plot(ax=ax, color="white", linewidth=3.0, zorder=13)
    fremont_outline.plot(ax=ax, color=PALETTE["city_edge"], linewidth=1.45, zorder=14)

    # Tract labels at representative points of the city-clipped polygons.
    for _, row in clipped.iterrows():
        pt = row.geometry.representative_point()
        label_point(ax, pt.x, pt.y, row["tract"], size=11)

    # Highway labels.
    label_clip = box(extent[0], extent[1], extent[2], extent[3])
    seen: set[str] = set()
    label_source = pd.concat(
        [highway.assign(_kind="hw"), arterial.assign(_kind="art")],
        ignore_index=True,
    ) if not highway.empty or not arterial.empty else pd.DataFrame()
    if not label_source.empty:
        for _, row in label_source.dropna(subset=["FULLNAME"]).iterrows():
            raw = row["FULLNAME"]
            if raw not in ROAD_LABELS or raw in seen:
                continue
            seg = longest_segment(row.geometry.intersection(label_clip))
            if seg is None or seg.length < 700:
                continue
            seen.add(raw)
            pt = seg.interpolate(0.42, normalized=True)
            txt = ax.text(
                pt.x, pt.y, ROAD_LABELS[raw],
                ha="center", va="center", fontsize=7,
                color="#3f3a32", zorder=16, style="italic",
            )
            txt.set_path_effects(halo(2.4))

    add_scale_bar(ax, length_miles=1.0)
    add_north_arrow(ax)


def draw_side_panel(ax: plt.Axes, locator_ax: plt.Axes, data: dict) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor(PALETTE["panel"])
    for spine in ax.spines.values():
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(1.15)

    clipped = data["clipped"]

    ax.text(0.07, 0.975, "LEGEND", fontsize=11, weight="bold",
            color=PALETTE["label_dark"], va="top")
    ax.plot([0.07, 0.93], [0.948, 0.948], color=PALETTE["title_bar"], linewidth=1.1)

    ax.text(0.07, 0.925, "2020 Census tracts", fontsize=9, weight="bold",
            color=PALETTE["label_dark"], va="top")
    ax.text(
        0.07, 0.900,
        "Colored area is the portion inside\nFremont city limits.",
        fontsize=7.4, color=PALETTE["label_muted"], va="top", linespacing=1.25,
    )

    y = 0.848
    sw_w, sw_h = 0.10, 0.028
    for _, row in clipped.iterrows():
        ax.add_patch(mpatches.Rectangle(
            (0.07, y), sw_w, sw_h,
            facecolor=row["fill"], edgecolor=PALETTE["tract_edge"],
            linewidth=0.6, transform=ax.transAxes,
        ))
        ax.text(
            0.20, y + sw_h / 2,
            f"Tract {row['tract']}",
            fontsize=8.4, color=PALETTE["label_dark"], va="center",
            transform=ax.transAxes,
        )
        ax.text(
            0.93, y + sw_h / 2,
            f"{row['area_sqmi']:.2f} sq mi",
            fontsize=7.6, color=PALETTE["label_muted"], va="center", ha="right",
            transform=ax.transAxes,
        )
        y -= 0.040

    y -= 0.012
    ax.plot([0.07, 0.93], [y + 0.018, y + 0.018], color=PALETTE["rule"], linewidth=0.6)

    ax.text(0.07, y, "Map symbols", fontsize=9, weight="bold",
            color=PALETTE["label_dark"], va="top")
    y -= 0.012

    handles = [
        mlines.Line2D([], [], color=PALETTE["city_edge"], linewidth=1.8, label="Fremont city limit"),
        mlines.Line2D([], [], color=PALETTE["tract_edge"], linewidth=1.1, label="Tract boundary (in city)"),
        mlines.Line2D(
            [], [], color=PALETTE["tract_edge_out"], linewidth=0.9,
            linestyle=(0, (4, 2.5)), label="Tract continues outside city",
        ),
        mlines.Line2D([], [], color=PALETTE["highway"], linewidth=1.6, label="U.S. highway"),
        mlines.Line2D([], [], color=PALETTE["arterial"], linewidth=1.1, label="Arterial / state route"),
        mlines.Line2D([], [], color=PALETTE["local"], linewidth=0.8, label="Local street"),
        mpatches.Patch(
            facecolor=PALETTE["water_fill"], edgecolor=PALETTE["water_edge"],
            linewidth=0.5, label="Lakes & ponds",
        ),
    ]
    legend = ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.05, y),
        frameon=False,
        fontsize=7.8,
        labelspacing=0.55,
        handlelength=2.1,
        handletextpad=0.7,
        borderpad=0.0,
    )
    ax.add_artist(legend)

    ax.text(
        0.07, 0.268,
        "Selection",
        fontsize=9, weight="bold", color=PALETTE["label_dark"], va="top",
    )
    ax.text(
        0.07, 0.242,
        f"{len(clipped)} tracts intersect Fremont city\n"
        f"(Census place GEOID {FREMONT_PLACE_GEOID}).\n"
        f"City land & water: {data['city_area_sqmi']:.2f} sq mi.",
        fontsize=7.4, color=PALETTE["label_muted"], va="top", linespacing=1.35,
    )

    ax.text(
        0.07, 0.168,
        "Locator",
        fontsize=9, weight="bold", color=PALETTE["label_dark"], va="top",
    )

    draw_locator(locator_ax, data["states"], data["counties"], data["fremont"].to_crs(data["states"].crs))

    ax.text(
        0.07, 0.018,
        "Dodge County is filled; Fremont\nis the orange point.",
        fontsize=7.2, color=PALETTE["label_muted"], va="bottom", linespacing=1.25,
    )


def draw_title(ax: plt.Axes) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor(PALETTE["title_bar"])
    ax.add_patch(mpatches.Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                    facecolor=PALETTE["title_bar"], edgecolor="none"))
    ax.text(
        0.012, 0.58, "U.S. Census Tracts  ·  City of Fremont, Nebraska",
        color=PALETTE["title_fg"], fontsize=16.5, weight="bold", va="center",
        transform=ax.transAxes,
    )
    ax.text(
        0.012, 0.20, "2020 Census geography  ·  Dodge County  ·  Portions of tracts inside incorporated city limits",
        color="#d6e2f0", fontsize=8.4, va="center", transform=ax.transAxes,
    )
    ax.text(
        0.988, 0.58, "TIGER/Line 2024",
        color=PALETTE["title_fg"], fontsize=9, ha="right", va="center",
        transform=ax.transAxes,
    )
    ax.text(
        0.988, 0.20, "Reference map",
        color="#d6e2f0", fontsize=8.2, ha="right", va="center",
        transform=ax.transAxes,
    )


def draw_footer(ax: plt.Axes) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor(PALETTE["page"])
    ax.text(
        0.0, 0.70,
        "Sources: U.S. Census Bureau TIGER/Line Shapefiles, 2024 (tracts, places, roads, hydrography); "
        "Cartographic Boundary Files, 20 million-scale (locator).  Place: Fremont city, NE (GEOID 3117670).",
        fontsize=7.3, color=PALETTE["label_muted"], va="center",
    )
    ax.text(
        0.0, 0.28,
        "Notes: 2020 Census tract definitions. Fills are clipped to the Census incorporated-place boundary; "
        "dashed lines show where a tract continues beyond city limits. Highway labels abbreviated from TIGER FULLNAME.  "
        f"Projection: NAD83 / Nebraska State Plane (EPSG:32104), meters.  Grid north.  Prepared 19 August 2026.",
        fontsize=7.3, color=PALETTE["label_muted"], va="center",
    )


def inches_rect(fig: plt.Figure, x: float, y: float, w: float, h: float) -> list[float]:
    fw, fh = fig.get_size_inches()
    return [x / fw, y / fh, w / fw, h / fh]


def build_map(output_dir: Path, basename: str = "fremont_ne_census_tracts") -> dict[str, Path]:
    configure_fonts()
    layers = load_layers(DATA_DIR)
    data = prepare_data(layers)

    clipped = data["clipped"]
    print("Census tracts within Fremont city:")
    print(
        clipped[["tract", "GEOID", "area_sqmi", "overlap_pct"]]
        .to_string(index=False, formatters={
            "area_sqmi": "{:.3f}".format,
            "overlap_pct": "{:.1f}%".format,
        })
    )
    print(f"City area: {data['city_area_sqmi']:.3f} sq mi")

    fig_w, fig_h = 17.0, 11.0
    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=PALETTE["page"])

    margin = 0.40
    gap = 0.14
    title_h = 0.62
    footer_h = 0.58
    title_y = fig_h - margin - title_h
    footer_y = margin
    body_y = footer_y + footer_h + gap
    body_h = title_y - gap - body_y
    map_w = 12.45
    side_x = margin + map_w + 0.16
    side_w = fig_w - margin - side_x

    title_ax = fig.add_axes(inches_rect(fig, margin, title_y, fig_w - 2 * margin, title_h))
    map_ax = fig.add_axes(inches_rect(fig, margin, body_y, map_w, body_h))
    side_ax = fig.add_axes(inches_rect(fig, side_x, body_y, side_w, body_h))
    foot_ax = fig.add_axes(inches_rect(fig, margin, footer_y, fig_w - 2 * margin, footer_h))

    # Locator nested in the lower portion of the side panel.
    loc_w, loc_h = side_w * 0.86, body_h * 0.175
    loc_x = side_x + (side_w - loc_w) / 2
    loc_y = body_y + body_h * 0.055
    locator_ax = fig.add_axes(inches_rect(fig, loc_x, loc_y, loc_w, loc_h))

    map_ratio = map_w / body_h
    extent = aspect_corrected_extent(tuple(data["fremont"].total_bounds), map_ratio, pad=0.07)

    draw_title(title_ax)
    draw_main_map(map_ax, data, extent)
    draw_side_panel(side_ax, locator_ax, data)
    draw_footer(foot_ax)

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{basename}.pdf"
    png_path = output_dir / f"{basename}.png"
    fig.savefig(pdf_path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.15)
    fig.savefig(png_path, facecolor=fig.get_facecolor(), dpi=220, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)

    print(f"Wrote {pdf_path}")
    print(f"Wrote {png_path}")
    return {"pdf": pdf_path, "png": png_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--basename", default="fremont_ne_census_tracts")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_map(args.output_dir, basename=args.basename)
