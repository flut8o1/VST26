"""
Visualisierung.py – Darstellung der gefundenen Route als PNG-Karte.

Dieses Modul übernimmt ausschließlich die Visualisierung. Es wandelt die vom
Suchalgorithmus gelieferte Knotenfolge in Zeichengeometrien um und erzeugt
daraus eine PNG-Karte mit Graph, Sperrzonen und hervorgehobener Route.

Die eigentliche Wegsuche findet in Algorithmus.py statt und ist von der
Darstellung getrennt.
"""

import geopandas as gpd
from shapely.geometry import Point, LineString

from utils import (
    WGS84, WEB_MERCATOR,
    get_fixed_extent_web_mercator,
    setup_map_figure, save_map_figure,
)


# =============================================================================
# Route als Geometrie
# =============================================================================

def build_route_geometries(grid, node_path):
    """
    Wandelt die Knotenfolge der Route in Zeichengeometrien (WGS84) um.

    Rückgabe:
        (route_line, route_points, start_point, end_point) – jeweils GeoSeries.
    """
    coords = [grid.node_xy(node_id) for node_id in node_path]

    route_line   = gpd.GeoSeries([LineString(coords)], crs=grid.metric_crs).to_crs(WGS84)
    route_points = gpd.GeoSeries([Point(xy) for xy in coords], crs=grid.metric_crs).to_crs(WGS84)
    start_point  = gpd.GeoSeries([Point(grid.start_xy)], crs=grid.metric_crs).to_crs(WGS84)
    end_point    = gpd.GeoSeries([Point(grid.end_xy)], crs=grid.metric_crs).to_crs(WGS84)

    return route_line, route_points, start_point, end_point


# =============================================================================
# Visualisierung als PNG
# =============================================================================

def visualize_route_png(
    *,
    grid,
    zones,
    route_line,
    route_points,
    start_point,
    end_point,
    output_png,
    show_map=True,
    satellite_background=True,
    basemap_zoom=13,
    fixed_extent=True,
    center_lat=48.137154,
    center_lon=11.576124,
    square_side_km=25,
):
    """
    Erzeugt eine PNG-Karte mit dem gesamten Graphen und der gefundenen Route.

    Ebenen (von unten nach oben):
        1. Satellitenhintergrund (optional)
        2. Sperrzonen – rot, transparent
        3. Alle Graphkanten – cyan, dünn
        4. Alle Graphknoten – gelb, klein
        5. Route-Linie – magenta, breit
        6. Route-Knoten – weiß mit schwarzem Rand
        7. Start- und Endpunkt – grün / rot, groß

    Rückgabe:
        Path-Objekt der gespeicherten PNG-Datei.
    """
    # Alle Layer in Web Mercator projizieren.
    nodes        = grid.nodes_gdf.to_crs(WEB_MERCATOR)
    edges        = grid.edges_gdf.to_crs(WEB_MERCATOR)
    route_line   = route_line.to_crs(WEB_MERCATOR)
    route_points = route_points.to_crs(WEB_MERCATOR)
    start_point  = start_point.to_crs(WEB_MERCATOR)
    end_point    = end_point.to_crs(WEB_MERCATOR)

    if zones is not None:
        zones = zones.to_crs(WEB_MERCATOR)

    # Kartenausschnitt bestimmen.
    if fixed_extent:
        minx, miny, maxx, maxy = get_fixed_extent_web_mercator(
            center_lat=center_lat,
            center_lon=center_lon,
            square_side_km=square_side_km,
        )
    else:
        minx, miny, maxx, maxy = edges.total_bounds

    fig, ax = setup_map_figure(
        minx, miny, maxx, maxy,
        satellite_background=satellite_background,
        basemap_zoom=basemap_zoom,
    )

    # Ebene 2: Sperrzonen
    if zones is not None:
        zones.plot(ax=ax, facecolor="red", edgecolor="red", linewidth=0.8, alpha=0.20, zorder=2)

    # Ebene 3: Alle Graphkanten
    edges.plot(ax=ax, color="cyan", linewidth=0.5, alpha=0.35, zorder=3)

    # Ebene 4: Alle Graphknoten
    nodes.plot(ax=ax, color="yellow", markersize=2, alpha=0.55, zorder=4)

    # Ebene 5: Route-Linie
    route_line.plot(ax=ax, color="magenta", linewidth=3.0, alpha=0.95, zorder=5)

    # Ebene 6: Route-Knoten
    route_points.plot(ax=ax, color="white", edgecolor="black", markersize=18, alpha=1.0, zorder=6)

    # Ebene 7: Start- und Endpunkt hervorheben.
    start_point.plot(ax=ax, color="lime", edgecolor="black", markersize=80, zorder=7)
    end_point.plot(ax=ax, color="red", edgecolor="black", markersize=80, zorder=7)

    return save_map_figure(fig, ax, output_png, title="Kürzester Weg im Graphen", show_map=show_map)
