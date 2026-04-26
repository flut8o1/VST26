from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import contextily as cx
import xyzservices.providers as xyz
from shapely.geometry import Point


WGS84 = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"


def _read_geojson(file_path):
    """
    Liest eine GeoJSON-Datei ein und setzt CRS auf WGS84, falls keines vorhanden ist.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {path}")

    gdf = gpd.read_file(path)

    if gdf.empty:
        raise ValueError(f"Datei enthält keine Features: {path}")

    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    return gdf


def _get_fixed_extent_web_mercator(
    center_lat,
    center_lon,
    square_side_km,
):
    """
    Erstellt einen quadratischen Kartenausschnitt in EPSG:3857.
    """

    center_point = gpd.GeoSeries(
        [Point(center_lon, center_lat)],
        crs=WGS84,
    ).to_crs(WEB_MERCATOR).iloc[0]

    half_side_m = square_side_km * 1000 / 2

    minx = center_point.x - half_side_m
    maxx = center_point.x + half_side_m
    miny = center_point.y - half_side_m
    maxy = center_point.y + half_side_m

    return minx, miny, maxx, maxy


def create_graph_png_map(
    nodes_geojson,
    edges_geojson,
    output_png,
    *,
    zones_geojson=None,
    show_map=True,
    satellite_background=True,
    basemap_zoom=13,
    fixed_extent=True,
    center_lat=48.137154,
    center_lon=11.576124,
    square_side_km=25,
):
    """
    Stellt einen bereits erzeugten Graphen als PNG dar.

    Wichtig:
        Diese Funktion erstellt KEINEN Graphen.
        Sie liest nur bestehende graph_nodes.geojson und graph_edges.geojson ein.

    Parameter:
        nodes_geojson:
            Datei mit den Graph-Knoten.

        edges_geojson:
            Datei mit den Graph-Kanten.

        output_png:
            Ausgabedatei für die PNG-Karte.

        zones_geojson:
            Optional: Sperrzonen-Datei, die transparent angezeigt wird.

        fixed_extent:
            True = fester 25-km-Ausschnitt um München Stadtmitte.
            False = Ausschnitt anhand der Graph-Ausdehnung.
    """

    nodes = _read_geojson(nodes_geojson)
    edges = _read_geojson(edges_geojson)

    nodes_plot = nodes.to_crs(WEB_MERCATOR)
    edges_plot = edges.to_crs(WEB_MERCATOR)

    zones_plot = None
    if zones_geojson is not None:
        zones = _read_geojson(zones_geojson)
        zones_plot = zones.to_crs(WEB_MERCATOR)

    if fixed_extent:
        minx, miny, maxx, maxy = _get_fixed_extent_web_mercator(
            center_lat=center_lat,
            center_lon=center_lon,
            square_side_km=square_side_km,
        )
    else:
        minx, miny, maxx, maxy = edges_plot.total_bounds

    fig, ax = plt.subplots(figsize=(14, 14))

    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)

    if satellite_background:
        cx.add_basemap(
            ax,
            source=xyz.Esri.WorldImagery,
            zoom=basemap_zoom,
        )

    # Optional: Sperrzonen anzeigen
    if zones_plot is not None:
        zones_plot.plot(
            ax=ax,
            facecolor="red",
            edgecolor="red",
            linewidth=0.8,
            alpha=0.20,
            zorder=2,
        )

    # Graph-Kanten
    edges_plot.plot(
        ax=ax,
        color="cyan",
        linewidth=0.7,
        alpha=0.75,
        zorder=3,
    )

    # Graph-Knoten
    nodes_plot.plot(
        ax=ax,
        color="yellow",
        markersize=4,
        alpha=0.95,
        zorder=4,
    )

    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)

    if fixed_extent:
        ax.set_title(
            f"Graph – {square_side_km} km Quadrat um München Stadtmitte",
            fontsize=16,
        )
    else:
        ax.set_title("Graph", fontsize=16)

    ax.set_axis_off()
    ax.set_aspect("equal")

    output_path = Path(output_png)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")

    if show_map:
        plt.show()
    else:
        plt.close(fig)

    return output_path


def main():
    nodes_geojson = "graph_nodes.geojson"
    edges_geojson = "graph_edges.geojson"
    zones_geojson = "drohnen_luftvo_zonen.geojson"

    output_png = "graph_karte.png"

    result_file = create_graph_png_map(
        nodes_geojson=nodes_geojson,
        edges_geojson=edges_geojson,
        zones_geojson=zones_geojson,
        output_png=output_png,
        show_map=True,
        satellite_background=True,
        basemap_zoom=13,
        fixed_extent=True,
        center_lat=48.137154,
        center_lon=11.576124,
        square_side_km=25,
    )

    print(f"Graph-Karte wurde erstellt: {result_file}")


if __name__ == "__main__":
    main()