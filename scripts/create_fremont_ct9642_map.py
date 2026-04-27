#!/usr/bin/env python3
"""Create a professional cartographic context map for Fremont, NE — Census Tract 9642.

Outputs (default ``output/`` directory):

* ``fremont_ne_ct9642_poverty_map.png`` — high-resolution raster.
* ``fremont_ne_ct9642_poverty_map.pdf`` — vector-quality PDF (print ready).
* ``shapefiles/`` directory containing the layers shown on the map
  (focus tract, neighborhood tracts with ACS poverty attributes, Fremont
  place boundary, Dodge County boundary, clipped TIGER roads, and the map
  extent), and a ``fremont_ne_ct9642_layers.zip`` archive of the same.

The script downloads U.S. Census TIGER/Line geometries and ACS 5-year
poverty estimates, joins them, and renders a publication-style map with
the focus tract highlighted, an inset locator, and a tract-level poverty
classification overlay.
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
from matplotlib.patches import FancyArrowPatch
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from shapely.geometry import box


ACS_YEAR = 2024
STATE_FIPS = "31"
DODGE_COUNTY_FIPS = "053"
TARGET_TRACT = "964200"
TARGET_GEOID = f"{STATE_FIPS}{DODGE_COUNTY_FIPS}{TARGET_TRACT}"
FREMONT_PLACE_GEOID = "3117670"
PROJECT_CRS = "EPSG:26914"  # NAD83 / UTM zone 14N — accurate for eastern Nebraska.
WGS84 = "EPSG:4326"

DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")
SHAPEFILE_SUBDIR = "shapefiles"

TIGER_URLS = {
    "tracts": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/TRACT/tl_{ACS_YEAR}_{STATE_FIPS}_tract.zip",
    "places": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/PLACE/tl_{ACS_YEAR}_{STATE_FIPS}_place.zip",
    "counties": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/COUNTY/tl_{ACS_YEAR}_us_county.zip",
    "roads": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/ROADS/tl_{ACS_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_roads.zip",
    "states": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/STATE/tl_{ACS_YEAR}_us_state.zip",
}

# Color palette — restrained, print-friendly.
PALETTE = {
    "background": "#ffffff",
    "land": "#f4efe4",
    "tract_edge": "#b6ad9c",
    "county_edge": "#7a7367",
    "city_edge": "#1f4e6b",
    "focus_fill": "#1f6feb",
    "focus_edge": "#0a2540",
    "road_case": "#ffffff",
    "road_major": "#5d5d5d",
    "road_local": "#cdc7bb",
    "label_dark": "#1d1d1d",
    "label_muted": "#5d5648",
    "frame": "#2b2b2b",
    "no_data": "#ececec",
}

POVERTY_BREAKS = [0, 5, 10, 15, 20, 30, 40]
POVERTY_LABELS = ["< 5%", "5 – 10%", "10 – 15%", "15 – 20%", "20 – 30%", "≥ 30%"]
POVERTY_COLORS = ["#fff5d6", "#ffd89c", "#fdae61", "#f46d43", "#d73027", "#7a0177"]


def fetch_acs_poverty(cache_path: Path) -> pd.DataFrame:
    """Download or load ACS poverty estimates for Dodge County tracts."""
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
    df["GEOID"] = df["state"] + df["county"] + df["tract"]
    df["pov_total"] = pd.to_numeric(df["B17001_001E"], errors="coerce")
    df["pov_count"] = pd.to_numeric(df["B17001_002E"], errors="coerce")
    df["pov_rate"] = (df["pov_count"] / df["pov_total"]) * 100
    return df[["GEOID", "NAME", "pov_total", "pov_count", "pov_rate"]]


def read_tiger_layer(name: str) -> gpd.GeoDataFrame:
    """Read a TIGER/Line zipfile URL using GeoPandas (cached by GDAL)."""
    return gpd.read_file(TIGER_URLS[name], engine="pyogrio")


def buffered_extent(bounds: Iterable[float], x_pad: float, y_pad: float) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    height = maxy - miny
    return (minx - width * x_pad, miny - height * y_pad, maxx + width * x_pad, maxy + height * y_pad)


def classify_poverty(values: pd.Series) -> pd.Series:
    """Bin a numeric Series into the configured poverty categories."""
    return pd.cut(values, bins=POVERTY_BREAKS, labels=POVERTY_LABELS, include_lowest=True, right=False)


def add_scale_bar(ax: plt.Axes, length_miles: int = 2) -> None:
    """Draw a clean two-segment scale bar in projected meters."""
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    length_m = length_miles * 1609.344
    segment_m = length_m / 2
    start_x = x0 + (x1 - x0) * 0.05
    start_y = y0 + (y1 - y0) * 0.05
    height = (y1 - y0) * 0.011

    for idx, facecolor in enumerate(["#1d1d1d", "#ffffff"]):
        rect = mpatches.Rectangle(
            (start_x + idx * segment_m, start_y),
            segment_m,
            height,
            facecolor=facecolor,
            edgecolor="#1d1d1d",
            linewidth=0.8,
            zorder=30,
        )
        ax.add_patch(rect)

    label_kwargs = dict(ha="center", va="bottom", fontsize=7.5, color=PALETTE["label_dark"], zorder=31)
    ax.text(start_x, start_y + height * 1.3, "0", **label_kwargs)
    ax.text(start_x + segment_m, start_y + height * 1.3, f"{length_miles // 2}", **label_kwargs)
    ax.text(start_x + length_m, start_y + height * 1.3, f"{length_miles} mi", **label_kwargs)


def add_north_arrow(ax: plt.Axes) -> None:
    """A minimal north arrow rendered in axes coordinates."""
    arrow = FancyArrowPatch(
        (0.955, 0.85),
        (0.955, 0.95),
        transform=ax.transAxes,
        arrowstyle="-|>",
        mutation_scale=16,
        linewidth=1.4,
        color=PALETTE["frame"],
        zorder=40,
    )
    ax.add_patch(arrow)
    ax.text(
        0.955, 0.965, "N",
        transform=ax.transAxes,
        ha="center", va="bottom",
        fontsize=11, weight="bold", color=PALETTE["frame"], zorder=41,
    )


def label_point(
    ax: plt.Axes,
    x: float, y: float,
    text: str,
    *,
    size: int = 10,
    weight: str = "normal",
    color: str = PALETTE["label_dark"],
    halo: bool = True,
    zorder: int = 25,
    italic: bool = False,
) -> None:
    style = "italic" if italic else "normal"
    txt = ax.text(
        x, y, text,
        ha="center", va="center",
        fontsize=size, weight=weight, color=color, style=style, zorder=zorder,
    )
    if halo:
        txt.set_path_effects([
            path_effects.Stroke(linewidth=2.6, foreground="white"),
            path_effects.Normal(),
        ])


def draw_locator_inset(parent_ax: plt.Axes, states: gpd.GeoDataFrame, dodge: gpd.GeoDataFrame) -> None:
    """Inset map: outline of Nebraska with Dodge County highlighted."""
    inset = inset_axes(
        parent_ax,
        width="20%", height="18%",
        loc="upper left",
        bbox_to_anchor=(0.012, -0.012, 1.0, 1.0),
        bbox_transform=parent_ax.transAxes,
        borderpad=0.6,
    )
    nebraska = states[states["STATEFP"] == STATE_FIPS].to_crs(PROJECT_CRS)
    nebraska.plot(ax=inset, color="#f4efe4", edgecolor="#7a7367", linewidth=1.0, zorder=2)
    dodge.plot(ax=inset, color=PALETTE["focus_fill"], alpha=0.9, edgecolor=PALETTE["focus_edge"], linewidth=1.2, zorder=3)

    minx, miny, maxx, maxy = nebraska.total_bounds
    pad = 0.02 * max(maxx - minx, maxy - miny)
    inset.set_xlim(minx - pad, maxx + pad)
    inset.set_ylim(miny - pad, maxy + pad)
    inset.set_aspect("equal")
    inset.set_xticks([])
    inset.set_yticks([])
    for spine in inset.spines.values():
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(0.8)
    inset.set_facecolor("#ffffff")
    inset.set_title("Nebraska", fontsize=7.5, color=PALETTE["label_dark"], pad=2, weight="bold")


def style_main_axes(ax: plt.Axes) -> None:
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for side, spine in ax.spines.items():
        spine.set_visible(True)
        spine.set_edgecolor(PALETTE["frame"])
        spine.set_linewidth(0.9)


def write_shapefiles(
    out_dir: Path,
    *,
    target: gpd.GeoDataFrame,
    tracts_view: gpd.GeoDataFrame,
    fremont: gpd.GeoDataFrame,
    dodge: gpd.GeoDataFrame,
    roads_view: gpd.GeoDataFrame,
    extent_polygon: gpd.GeoDataFrame,
) -> Path:
    """Write each map layer to its own shapefile and bundle them into a zip.

    Shapefiles are written in the projected CRS used by the map
    (NAD83 / UTM zone 14N) so distances and areas are immediately usable
    in downstream GIS workflows.
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    layers: dict[str, gpd.GeoDataFrame] = {
        "ct9642_focus_tract": target,
        "neighborhood_tracts_acs": tracts_view,
        "fremont_place": fremont,
        "dodge_county": dodge,
        "roads_clipped": roads_view,
        "map_extent": extent_polygon,
    }

    for name, gdf in layers.items():
        if gdf is None or gdf.empty:
            continue
        layer_dir = out_dir / name
        layer_dir.mkdir(parents=True, exist_ok=True)
        # Shapefile field names are limited to 10 characters; reduce defensively.
        truncated = gdf.copy()
        rename: dict[str, str] = {}
        seen: set[str] = set()
        for col in truncated.columns:
            if col == "geometry":
                continue
            short = col[:10]
            base = short
            i = 1
            while short in seen:
                short = f"{base[:8]}{i:02d}"
                i += 1
            seen.add(short)
            if short != col:
                rename[col] = short
        if rename:
            truncated = truncated.rename(columns=rename)
        truncated.to_file(layer_dir / f"{name}.shp", driver="ESRI Shapefile", engine="pyogrio")

    archive = out_dir.parent / "fremont_ne_ct9642_layers.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_dir.parent))
    return archive


def build_map(
    output_dir: Path,
    *,
    basename: str = "fremont_ne_ct9642_poverty_map",
    write_shp: bool = True,
) -> dict[str, Path]:
    DATA_DIR.mkdir(exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    acs = fetch_acs_poverty(DATA_DIR / f"acs_{ACS_YEAR}_dodge_county_poverty.json")
    tracts = read_tiger_layer("tracts")
    places = read_tiger_layer("places")
    counties = read_tiger_layer("counties")
    roads = read_tiger_layer("roads")
    states = read_tiger_layer("states")

    dodge = counties[(counties["STATEFP"] == STATE_FIPS) & (counties["COUNTYFP"] == DODGE_COUNTY_FIPS)].to_crs(PROJECT_CRS)
    fremont = places[places["GEOID"] == FREMONT_PLACE_GEOID].to_crs(PROJECT_CRS)
    dodge_tracts = tracts[(tracts["STATEFP"] == STATE_FIPS) & (tracts["COUNTYFP"] == DODGE_COUNTY_FIPS)]
    dodge_tracts = dodge_tracts.merge(acs, on="GEOID", how="left").to_crs(PROJECT_CRS)
    roads = roads.to_crs(PROJECT_CRS)

    target = dodge_tracts[dodge_tracts["GEOID"] == TARGET_GEOID]
    if target.empty:
        raise RuntimeError(f"Could not find target Census Tract GEOID {TARGET_GEOID}.")

    extent = buffered_extent(target.total_bounds, x_pad=1.25, y_pad=1.00)
    extent_geom = box(*extent)
    extent_polygon = gpd.GeoDataFrame(
        {"name": ["map_extent"]}, geometry=[extent_geom], crs=PROJECT_CRS,
    )

    tracts_view = dodge_tracts[dodge_tracts.intersects(extent_geom)].copy()
    tracts_view["pov_class"] = classify_poverty(tracts_view["pov_rate"])
    roads_view = roads[roads.intersects(extent_geom)].copy()

    cmap = mcolors.ListedColormap(POVERTY_COLORS)
    cmap.set_bad(PALETTE["no_data"])
    norm = mcolors.BoundaryNorm(POVERTY_BREAKS, cmap.N)

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
    })

    fig, ax = plt.subplots(figsize=(12, 9.5), dpi=200)
    fig.patch.set_facecolor(PALETTE["background"])
    ax.set_facecolor(PALETTE["land"])

    dodge.boundary.plot(ax=ax, color=PALETTE["county_edge"], linewidth=1.2, zorder=2)

    tracts_view.plot(
        ax=ax,
        column="pov_rate",
        cmap=cmap,
        norm=norm,
        alpha=0.55,
        edgecolor=PALETTE["tract_edge"],
        linewidth=0.55,
        zorder=3,
        missing_kwds={"color": PALETTE["no_data"], "edgecolor": "#bdbdbd", "hatch": "///"},
    )

    major_codes = {"S1100", "S1200"}
    major_roads = roads_view[roads_view["MTFCC"].isin(major_codes)]
    local_roads = roads_view[~roads_view["MTFCC"].isin(major_codes)]
    local_roads.plot(ax=ax, color=PALETTE["road_local"], linewidth=0.4, alpha=0.85, zorder=4)
    major_roads.plot(ax=ax, color=PALETTE["road_case"], linewidth=2.8, alpha=0.95, zorder=5)
    major_roads.plot(ax=ax, color=PALETTE["road_major"], linewidth=1.1, alpha=0.95, zorder=6)

    fremont.boundary.plot(ax=ax, color=PALETTE["city_edge"], linewidth=1.6, linestyle=(0, (5, 3)), zorder=8)

    target_buffer = target.copy()
    target_buffer["geometry"] = target.buffer(60)
    target_buffer.plot(ax=ax, color="#0a2540", alpha=0.18, zorder=9)
    target.plot(ax=ax, color=PALETTE["focus_fill"], alpha=0.45, edgecolor=PALETTE["focus_edge"], linewidth=2.6, zorder=10)
    target.boundary.plot(ax=ax, color=PALETTE["focus_edge"], linewidth=2.8, zorder=11)

    # Place the city label inside the visible extent, above the focus tract
    # so it never overlaps the highlighted polygon or its label.
    target_geom = target.geometry.iloc[0]
    target_top = target_geom.bounds[3]
    label_x = (extent[0] + extent[2]) / 2
    label_y = min(extent[3] - (extent[3] - extent[1]) * 0.10,
                  target_top + (extent[3] - extent[1]) * 0.18)
    label_point(ax, label_x, label_y, "City of Fremont",
                size=13, weight="bold", color=PALETTE["city_edge"])
    label_point(
        ax,
        extent[0] + (extent[2] - extent[0]) * 0.10,
        extent[1] + (extent[3] - extent[1]) * 0.55,
        "Dodge County",
        size=12, italic=True, color=PALETTE["label_muted"],
    )

    target_pt = target.geometry.iloc[0].representative_point()
    label_point(ax, target_pt.x, target_pt.y + 380, "Census Tract 9642",
                size=11, weight="bold", color=PALETTE["focus_edge"])

    # Place at most one label per unique road name and only inside the visible
    # extent so labels don't drift past the frame or under the locator inset.
    inset_clip = box(
        extent[0], extent[1] + (extent[3] - extent[1]) * 0.78,
        extent[0] + (extent[2] - extent[0]) * 0.30, extent[3],
    )
    legend_clip = box(
        extent[0] + (extent[2] - extent[0]) * 0.62, extent[1],
        extent[2], extent[1] + (extent[3] - extent[1]) * 0.30,
    )
    seen_names: set[str] = set()
    for _, row in major_roads.dropna(subset=["FULLNAME"]).iterrows():
        name = row["FULLNAME"]
        if not name or name in seen_names:
            continue
        if row.geometry.length < 1200:
            continue
        if not any(token in name for token in ["US Hwy", "State Hwy", "Broad St", "Main St"]):
            continue
        clipped = row.geometry.intersection(extent_geom)
        if clipped.is_empty:
            continue
        clipped = clipped.difference(inset_clip).difference(legend_clip)
        if clipped.is_empty or clipped.length < 600:
            continue
        # MultiLineString fallback: pick the longest segment for label placement.
        if clipped.geom_type == "MultiLineString":
            segment = max(list(clipped.geoms), key=lambda g: g.length)
        else:
            segment = clipped
        seen_names.add(name)
        point = segment.interpolate(0.5, normalized=True)
        if point.is_empty:
            continue
        label_point(ax, point.x, point.y, name, size=7, color="#3a3a3a", weight="normal", halo=True, zorder=15)

    ax.set_xlim(extent[0], extent[2])
    ax.set_ylim(extent[1], extent[3])
    style_main_axes(ax)

    add_scale_bar(ax, length_miles=2)
    add_north_arrow(ax)
    draw_locator_inset(ax, states, dodge)

    sm = ScalarMappable(norm=norm, cmap=cmap)
    cbar = fig.colorbar(
        sm, ax=ax,
        orientation="horizontal",
        fraction=0.032, pad=0.04, shrink=0.55,
        ticks=POVERTY_BREAKS,
        spacing="proportional",
    )
    cbar.set_label(
        f"ACS {ACS_YEAR} 5-year poverty rate by census tract (%)",
        fontsize=9, labelpad=4,
    )
    cbar.ax.tick_params(labelsize=8)
    tick_labels = [str(b) for b in POVERTY_BREAKS[:-1]] + [f"{POVERTY_BREAKS[-1]}+"]
    cbar.ax.set_xticklabels(tick_labels)
    cbar.outline.set_edgecolor(PALETTE["frame"])
    cbar.outline.set_linewidth(0.7)

    legend_handles = [
        mpatches.Patch(facecolor=PALETTE["focus_fill"], edgecolor=PALETTE["focus_edge"], linewidth=2.0,
                       alpha=0.55, label="Focus area: Census Tract 9642"),
        mlines.Line2D([], [], color=PALETTE["city_edge"], linestyle=(0, (5, 3)), linewidth=1.6,
                      label="City of Fremont boundary"),
        mlines.Line2D([], [], color=PALETTE["road_major"], linewidth=1.4, label="Major roads"),
        mlines.Line2D([], [], color=PALETTE["road_local"], linewidth=1.2, label="Local roads"),
        mlines.Line2D([], [], color=PALETTE["county_edge"], linewidth=1.2, label="Dodge County boundary"),
        mpatches.Patch(facecolor=PALETTE["no_data"], edgecolor="#bdbdbd", hatch="///", label="No ACS data"),
    ]
    legend = ax.legend(
        handles=legend_handles,
        loc="lower right",
        frameon=True,
        framealpha=0.95,
        fontsize=8.5,
        title="Map layers",
        title_fontsize=9,
        borderpad=0.7,
    )
    legend.get_frame().set_edgecolor(PALETTE["frame"])
    legend.get_frame().set_linewidth(0.6)

    target_rate = target["pov_rate"].iloc[0]
    target_pop = target["pov_total"].iloc[0]
    target_pov_count = target["pov_count"].iloc[0]

    fig.suptitle(
        "Fremont, Nebraska  ·  Census Tract 9642 — Cartographic Context",
        fontsize=16, weight="bold", y=0.965, color=PALETTE["label_dark"],
    )
    ax.set_title(
        f"Tract-level poverty classification (ACS {ACS_YEAR}, 5-year)   |   "
        f"CT 9642 poverty rate: {target_rate:.1f}%   "
        f"({int(target_pov_count):,} of {int(target_pop):,} persons)",
        fontsize=10, pad=8, color=PALETTE["label_dark"],
    )

    fig.text(
        0.012, 0.008,
        "Projection: NAD83 / UTM Zone 14N (EPSG:26914)",
        ha="left", va="bottom", fontsize=7.5, color=PALETTE["label_muted"],
    )
    fig.text(
        0.988, 0.008,
        f"Sources: U.S. Census Bureau TIGER/Line {ACS_YEAR}; "
        f"ACS {ACS_YEAR} 5-Year Estimates, table B17001",
        ha="right", va="bottom", fontsize=7.5, color=PALETTE["label_muted"],
    )

    fig.subplots_adjust(left=0.035, right=0.985, top=0.92, bottom=0.10)

    png_path = output_dir / f"{basename}.png"
    pdf_path = output_dir / f"{basename}.pdf"
    fig.savefig(png_path, facecolor=fig.get_facecolor())
    fig.savefig(pdf_path, facecolor=fig.get_facecolor())
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
            extent_polygon=extent_polygon,
        )
        written["shapefiles_dir"] = shp_dir
        written["shapefiles_zip"] = archive
        print(f"Wrote shapefiles to {shp_dir}/")
        print(f"Wrote {archive}")

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Output directory (defaults to ./output).",
    )
    parser.add_argument(
        "--basename",
        type=str,
        default="fremont_ne_ct9642_poverty_map",
        help="Filename stem used for the rendered PNG and PDF.",
    )
    parser.add_argument(
        "--no-shapefiles",
        action="store_true",
        help="Skip writing shapefile exports.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_map(args.output_dir, basename=args.basename, write_shp=not args.no_shapefiles)
