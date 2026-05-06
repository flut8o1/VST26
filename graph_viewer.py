"""
graph_viewer.py – Visualisierung des Navigationsgraphen als PNG-Karte.

Liest bestehende graph_nodes.geojson und graph_edges.geojson ein und
stellt den Graphen (mit optionalen Sperrzonen) als hochauflösendes PNG dar.

Diese Funktion erstellt KEINEN Graphen – sie visualisiert nur einen
bereits erzeugten.
"""

from utils import (
    WEB_MERCATOR,
    read_geojson, get_fixed_extent_web_mercator,
    setup_map_figure, save_map_figure,
)


# =============================================================================
# Visualisierungsfunktion
# =============================================================================

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
    Stellt einen bestehenden Navigationsgraphen als PNG-Karte dar.

    nodes_geojson:
        Datei mit den Graph-Knoten (graph_nodes.geojson).
    edges_geojson:
        Datei mit den Graph-Kanten (graph_edges.geojson).
    output_png:
        Zieldatei für die PNG-Ausgabe.
    zones_geojson:
        Optional: Sperrzonen-Datei, die transparent als rote Flächen
        im Hintergrund angezeigt wird.
    fixed_extent:
        True  – Fester Quadratausschnitt um den angegebenen Mittelpunkt.
        False – Ausschnitt wird aus den Kantengrenzen abgeleitet.
    """

    # --- Daten einlesen und in Web Mercator projizieren ---

    nodes_plot = read_geojson(nodes_geojson).to_crs(WEB_MERCATOR)
    edges_plot = read_geojson(edges_geojson).to_crs(WEB_MERCATOR)

    zones_plot = None
    if zones_geojson is not None:
        zones_plot = read_geojson(zones_geojson).to_crs(WEB_MERCATOR)

    # --- Kartenausschnitt bestimmen ---

    if fixed_extent:
        minx, miny, maxx, maxy = get_fixed_extent_web_mercator(
            center_lat=center_lat,
            center_lon=center_lon,
            square_side_km=square_side_km,
        )
    else:
        minx, miny, maxx, maxy = edges_plot.total_bounds

    # --- Figure aufbauen ---

    fig, ax = setup_map_figure(
        minx, miny, maxx, maxy,
        satellite_background=satellite_background,
        basemap_zoom=basemap_zoom,
    )

    # Sperrzonen als transparente rote Flächen im Hintergrund.
    if zones_plot is not None:
        zones_plot.plot(ax=ax, facecolor="red", edgecolor="red", linewidth=0.8, alpha=0.20, zorder=2)

    # Graph-Kanten als dünne cyan Linien.
    edges_plot.plot(ax=ax, color="cyan", linewidth=0.7, alpha=0.75, zorder=3)

    # Graph-Knoten als kleine gelbe Punkte.
    nodes_plot.plot(ax=ax, color="yellow", markersize=4, alpha=0.95, zorder=4)

    # --- Kartentitel und Speichern ---

    if fixed_extent:
        title = f"Graph – {square_side_km} km Quadrat um München Stadtmitte"
    else:
        title = "Graph"

    return save_map_figure(fig, ax, output_png, title=title, show_map=show_map)
