#!/usr/bin/env python3
"""Create a Fremont, Nebraska map focused on Census Tract 9642.

The script downloads U.S. Census TIGER/Line geometries and ACS 5-year
poverty counts, then renders a publication-style map with CT 9642
highlighted over a subtle tract-level poverty-rate choropleth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import matplotlib.colors as colors
import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
import requests
from matplotlib.cm import ScalarMappable
from matplotlib.patches import FancyArrowPatch
from shapely.geometry import box


ACS_YEAR = 2024
STATE_FIPS = "31"
DODGE_COUNTY_FIPS = "053"
TARGET_TRACT = "964200"
TARGET_GEOID = f"{STATE_FIPS}{DODGE_COUNTY_FIPS}{TARGET_TRACT}"
FREMONT_PLACE_GEOID = "3117670"
PROJECT_CRS = "EPSG:26914"  # NAD83 / UTM zone 14N; good local distance behavior.

DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")

TIGER_URLS = {
    "tracts": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/TRACT/tl_{ACS_YEAR}_{STATE_FIPS}_tract.zip",
    "places": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/PLACE/tl_{ACS_YEAR}_{STATE_FIPS}_place.zip",
    "counties": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/COUNTY/tl_{ACS_YEAR}_us_county.zip",
    "roads": f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/ROADS/tl_{ACS_YEAR}_{STATE_FIPS}{DODGE_COUNTY_FIPS}_roads.zip",
}


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
    df["poverty_total"] = pd.to_numeric(df["B17001_001E"], errors="coerce")
    df["poverty_count"] = pd.to_numeric(df["B17001_002E"], errors="coerce")
    df["poverty_rate"] = (df["poverty_count"] / df["poverty_total"]) * 100
    return df[["GEOID", "NAME", "poverty_total", "poverty_count", "poverty_rate"]]


def read_tiger_layer(name: str) -> gpd.GeoDataFrame:
    """Read a TIGER/Line zipfile URL using GeoPandas."""
    return gpd.read_file(TIGER_URLS[name], engine="pyogrio")


def buffered_extent(bounds: Iterable[float], x_pad: float, y_pad: float) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    height = maxy - miny
    return (minx - width * x_pad, miny - height * y_pad, maxx + width * x_pad, maxy + height * y_pad)


def add_scale_bar(ax: plt.Axes, length_miles: int = 2) -> None:
    """Draw a simple alternating black/white scale bar."""
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    length_m = length_miles * 1609.344
    segment_m = length_m / 2
    start_x = x0 + (x1 - x0) * 0.06
    start_y = y0 + (y1 - y0) * 0.07
    height = (y1 - y0) * 0.012

    for idx, facecolor in enumerate(["black", "white"]):
        rect = mpatches.Rectangle(
            (start_x + idx * segment_m, start_y),
            segment_m,
            height,
            facecolor=facecolor,
            edgecolor="black",
            linewidth=0.8,
            zorder=20,
        )
        ax.add_patch(rect)
    ax.text(start_x, start_y + height * 2.2, "0", ha="center", va="bottom", fontsize=8)
    ax.text(start_x + segment_m, start_y + height * 2.2, f"{length_miles // 2}", ha="center", va="bottom", fontsize=8)
    ax.text(start_x + length_m, start_y + height * 2.2, f"{length_miles} mi", ha="center", va="bottom", fontsize=8)


def add_north_arrow(ax: plt.Axes) -> None:
    """Add a north arrow in axes coordinates."""
    arrow = FancyArrowPatch(
        (0.93, 0.82),
        (0.93, 0.94),
        transform=ax.transAxes,
        arrowstyle="-|>",
        mutation_scale=18,
        linewidth=1.3,
        color="#1f1f1f",
        zorder=30,
    )
    ax.add_patch(arrow)
    ax.text(0.93, 0.955, "N", transform=ax.transAxes, ha="center", va="bottom", fontsize=12, weight="bold")


def label_geometry(ax: plt.Axes, geometry, label: str, *, dy: float = 0, size: int = 11, weight: str = "normal") -> None:
    point = geometry.representative_point()
    ax.text(
        point.x,
        point.y + dy,
        label,
        ha="center",
        va="center",
        fontsize=size,
        weight=weight,
        color="#1f1f1f",
        path_effects=[],
        zorder=25,
    )


def build_map(output_path: Path) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    acs = fetch_acs_poverty(DATA_DIR / f"acs_{ACS_YEAR}_dodge_county_poverty.json")
    tracts = read_tiger_layer("tracts")
    places = read_tiger_layer("places")
    counties = read_tiger_layer("counties")
    roads = read_tiger_layer("roads")

    dodge = counties[(counties["STATEFP"] == STATE_FIPS) & (counties["COUNTYFP"] == DODGE_COUNTY_FIPS)].to_crs(PROJECT_CRS)
    fremont = places[places["GEOID"] == FREMONT_PLACE_GEOID].to_crs(PROJECT_CRS)
    dodge_tracts = tracts[(tracts["STATEFP"] == STATE_FIPS) & (tracts["COUNTYFP"] == DODGE_COUNTY_FIPS)]
    dodge_tracts = dodge_tracts.merge(acs, on="GEOID", how="left").to_crs(PROJECT_CRS)
    roads = roads.to_crs(PROJECT_CRS)

    target = dodge_tracts[dodge_tracts["GEOID"] == TARGET_GEOID]
    if target.empty:
        raise RuntimeError(f"Could not find target Census Tract GEOID {TARGET_GEOID}.")

    extent = buffered_extent(target.total_bounds, x_pad=1.25, y_pad=1.00)
    extent_polygon = gpd.GeoSeries([box(*extent)], crs=PROJECT_CRS)
    tracts_view = dodge_tracts[dodge_tracts.intersects(extent_polygon.iloc[0])]
    roads_view = roads[roads.intersects(extent_polygon.iloc[0])]

    fig, ax = plt.subplots(figsize=(11, 9), dpi=220)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f6f2e9")

    dodge.boundary.plot(ax=ax, color="#9a958a", linewidth=1.1, zorder=2)
    tracts_view.plot(
        ax=ax,
        column="poverty_rate",
        cmap="YlOrRd",
        norm=colors.Normalize(vmin=0, vmax=max(25, tracts_view["poverty_rate"].max())),
        alpha=0.28,
        edgecolor="#aa9e8e",
        linewidth=0.6,
        zorder=3,
        missing_kwds={"color": "#efefef", "edgecolor": "#bdbdbd", "hatch": "///", "label": "No ACS data"},
    )

    major_roads = roads_view[roads_view["MTFCC"].isin(["S1100", "S1200"])]
    local_roads = roads_view[~roads_view["MTFCC"].isin(["S1100", "S1200"])]
    local_roads.plot(ax=ax, color="#d5d0c6", linewidth=0.35, alpha=0.75, zorder=4)
    major_roads.plot(ax=ax, color="#ffffff", linewidth=2.6, alpha=0.95, zorder=5)
    major_roads.plot(ax=ax, color="#707070", linewidth=1.05, alpha=0.95, zorder=6)

    fremont.boundary.plot(ax=ax, color="#245b7d", linewidth=1.4, linestyle="--", zorder=8)
    target.plot(ax=ax, color="#2f80ed", alpha=0.40, edgecolor="#08306b", linewidth=3.0, zorder=10)
    target.boundary.plot(ax=ax, color="#08306b", linewidth=3.2, zorder=11)

    label_geometry(ax, fremont.geometry.iloc[0], "City of Fremont", size=12, weight="bold")
    dodge_label_point = dodge.geometry.iloc[0].representative_point()
    ax.text(
        extent[0] + (extent[2] - extent[0]) * 0.76,
        extent[1] + (extent[3] - extent[1]) * 0.18,
        "Dodge County",
        ha="center",
        va="center",
        fontsize=12,
        color="#5a5148",
        style="italic",
        zorder=24,
    )
    label_geometry(ax, target.geometry.iloc[0], "Census Tract 9642", dy=450, size=11, weight="bold")

    for _, row in major_roads.dropna(subset=["FULLNAME"]).drop_duplicates("FULLNAME").iterrows():
        if row.geometry.length < 700:
            continue
        point = row.geometry.interpolate(0.5, normalized=True)
        name = row["FULLNAME"]
        if any(token in name for token in ["US Hwy", "State Hwy", "Broad St", "Main St", "23rd", "6th"]):
            ax.text(point.x, point.y, name, fontsize=6.5, color="#4d4d4d", ha="center", va="center", zorder=15)

    ax.set_xlim(extent[0], extent[2])
    ax.set_ylim(extent[1], extent[3])
    ax.set_aspect("equal")
    ax.axis("off")

    add_scale_bar(ax, length_miles=2)
    add_north_arrow(ax)

    poverty_handle = ScalarMappable(
        norm=colors.Normalize(vmin=0, vmax=max(25, tracts_view["poverty_rate"].max())),
        cmap="YlOrRd",
    )
    cbar = fig.colorbar(poverty_handle, ax=ax, orientation="horizontal", fraction=0.035, pad=0.018, shrink=0.55)
    cbar.set_label("ACS poverty rate by census tract (%) - subtle contextual overlay", fontsize=9)
    cbar.ax.tick_params(labelsize=8)

    legend_handles = [
        mpatches.Patch(facecolor="#2f80ed", edgecolor="#08306b", linewidth=2.2, alpha=0.55, label="Highlighted focus: Census Tract 9642"),
        mlines.Line2D([], [], color="#245b7d", linestyle="--", linewidth=1.4, label="City of Fremont boundary"),
        mlines.Line2D([], [], color="#707070", linewidth=1.2, label="Major roads"),
        mlines.Line2D([], [], color="#9a958a", linewidth=1.1, label="Dodge County boundary"),
    ]
    ax.legend(handles=legend_handles, loc="lower right", frameon=True, framealpha=0.92, fontsize=8.5, title="Map layers", title_fontsize=9)

    target_rate = target["poverty_rate"].iloc[0]
    fig.suptitle("Fremont, Nebraska - Census Tract 9642 Context Map", fontsize=16, weight="bold", y=0.97)
    ax.set_title(
        f"Dodge County tracts shown with subtle ACS {ACS_YEAR} 5-year poverty-rate overlay; "
        f"CT 9642 poverty rate: {target_rate:.1f}%",
        fontsize=10,
        pad=8,
    )
    fig.text(
        0.5,
        0.02,
        "Source: U.S. Census Bureau, American Community Survey "
        f"{ACS_YEAR} 5-Year Estimates, table B17001 (poverty status); "
        f"U.S. Census Bureau TIGER/Line Shapefiles {ACS_YEAR} (tracts, places, counties, roads).",
        ha="center",
        va="bottom",
        fontsize=8,
        color="#333333",
    )

    fig.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Wrote {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "fremont_ne_ct9642_poverty_map.png",
        help="Output image path.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_map(args.output)
