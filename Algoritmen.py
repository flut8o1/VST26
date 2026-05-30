"""
Algoritmen.py – Kürzeste-Weg-Suche auf dem Matrix-Gitter.

Erhält das NavigationGrid (Matrix-Darstellung) im Speicher und sucht den
kürzesten Weg zwischen Start- und Endknoten mit Dijkstra oder A*.

Die Algorithmen arbeiten direkt auf den (row, col)-Indizes der Matrix:
Die Nachbarn eines Knotens werden über die Passierbarkeits-Arrays bestimmt –
es wird keine Adjazenzliste aufgebaut. Start- und Endknoten liegen außerhalb
des Gitters und sind über ihre vorab gespeicherten Verbindungen angebunden.

Einzige Ausgabedatei ist die PNG-Routenkarte.
"""

import math
from heapq import heappush, heappop

import geopandas as gpd
from shapely.geometry import Point, LineString

from utils import (
    WGS84, WEB_MERCATOR,
    get_fixed_extent_web_mercator,
    setup_map_figure, save_map_figure,
)


# =============================================================================
# Nachbarschaft auf der Matrix
# =============================================================================

def _make_neighbor_function(grid):
    """
    Erstellt eine Funktion, die zu einem Knoten seine Nachbarn liefert.

    Für Gitterknoten werden die Nachbarn über die Passierbarkeits-Arrays der
    Matrix bestimmt (4 orthogonale, optional 4 diagonale Richtungen). Start-
    und Endknoten nutzen ihre gespeicherten Verbindungen.

    Rückgabe einer Nachbarfunktion node_id -> Liste von (nachbar_id, kosten_m).
    """
    n_rows, n_cols   = grid.n_rows, grid.n_cols
    passable         = grid.passable
    spacing          = grid.spacing_m
    start_id, end_id = grid.start_id, grid.end_id

    # Rückverbindungen: von einem Gitterknoten zurück zum Start-/Endknoten.
    back = {}
    for grid_id, length in grid.start_connections:
        back.setdefault(grid_id, []).append((start_id, length))
    for grid_id, length in grid.end_connections:
        back.setdefault(grid_id, []).append((end_id, length))

    def neighbors(node_id):
        if node_id == start_id:
            return list(grid.start_connections)
        if node_id == end_id:
            return list(grid.end_connections)

        row, col = divmod(node_id, n_cols)
        result   = []

        # Orthogonale Nachbarn.
        if col + 1 < n_cols and passable["E"][row, col]:
            result.append((row * n_cols + (col + 1), spacing))
        if col - 1 >= 0 and passable["W"][row, col]:
            result.append((row * n_cols + (col - 1), spacing))
        if row + 1 < n_rows and passable["N"][row, col]:
            result.append(((row + 1) * n_cols + col, spacing))
        if row - 1 >= 0 and passable["S"][row, col]:
            result.append(((row - 1) * n_cols + col, spacing))

        # Anbindung an Start-/Endknoten, falls dieser Gitterknoten verbunden ist.
        if node_id in back:
            result.extend(back[node_id])

        return result

    return neighbors


def _reconstruct_path(previous, start_id, end_id):
    """Rekonstruiert die Knotenfolge vom Start zum Ziel aus dem previous-Dict."""
    path    = [end_id]
    current = end_id

    while current != start_id:
        current = previous[current]
        path.append(current)

    path.reverse()
    return path


# =============================================================================
# Suchalgorithmen
# =============================================================================

def _dijkstra(neighbors, start_id, end_id):
    """
    Dijkstra-Algorithmus zur Suche des kürzesten Weges.

    Verwendet einen Min-Heap für effiziente Extraktion des nächstgelegenen Knotens.

    Rückgabe:
        (node_path, route_length_m)
    """
    distances = {start_id: 0.0}
    previous  = {}
    queue     = [(0.0, start_id)]
    visited   = set()

    while queue:
        current_distance, current_node = heappop(queue)

        if current_node in visited:
            continue

        visited.add(current_node)

        if current_node == end_id:
            break

        for neighbor, cost in neighbors(current_node):
            new_distance = current_distance + cost

            if new_distance < distances.get(neighbor, float("inf")):
                distances[neighbor] = new_distance
                previous[neighbor]  = current_node
                heappush(queue, (new_distance, neighbor))

    return _reconstruct_path(previous, start_id, end_id), distances[end_id]


def _astar(neighbors, heuristic, start_id, end_id):
    """
    A*-Algorithmus zur Suche des kürzesten Weges.

    Nutzt die euklidische Luftlinienentfernung als zulässige Heuristik,
    die den tatsächlichen Restweg nie überschätzt.

    Rückgabe:
        (node_path, route_length_m)
    """
    g_score  = {start_id: 0.0}
    previous = {}
    queue    = [(heuristic(start_id), 0.0, start_id)]
    visited  = set()

    while queue:
        _, current_g, current_node = heappop(queue)

        if current_node in visited:
            continue

        visited.add(current_node)

        if current_node == end_id:
            break

        for neighbor, cost in neighbors(current_node):
            tentative_g = current_g + cost

            if tentative_g < g_score.get(neighbor, float("inf")):
                g_score[neighbor]  = tentative_g
                previous[neighbor] = current_node
                heappush(queue, (tentative_g + heuristic(neighbor), tentative_g, neighbor))

    return _reconstruct_path(previous, start_id, end_id), g_score[end_id]


# =============================================================================
# Route als Geometrie
# =============================================================================

def _build_route_geometries(grid, node_path):
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

def _visualize_route_png(
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


# =============================================================================
# Haupt-Pipeline-Funktion
# =============================================================================

def find_path_and_visualize(
    grid,
    *,
    zones=None,
    output_png="route_karte.png",
    algorithm="dijkstra",
    show_map=True,
    satellite_background=True,
    basemap_zoom=13,
    fixed_extent=True,
    center_lat=48.137154,
    center_lon=11.576124,
    square_side_km=25,
):
    """
    Sucht den kürzesten Weg im Navigationsgitter und visualisiert ihn.

    grid:
        NavigationGrid (wie von Graph.create_navigation_graph erzeugt).
    zones:
        Optionales GeoDataFrame der Sperrzonen für die Kartendarstellung.

    algorithm:
        "dijkstra" – Dijkstra-Algorithmus (optimal, keine Heuristik).
        "astar"    – A*-Algorithmus (schneller durch euklidische Heuristik).

    Rückgabe:
        Dict mit Ergebniskennzahlen (Algorithmus, Länge, Knotenzahl, PNG-Pfad).
    """
    neighbors = _make_neighbor_function(grid)

    # --- Wegsuche direkt auf der Matrix ---

    if algorithm == "dijkstra":
        node_path, route_length_m = _dijkstra(neighbors, grid.start_id, grid.end_id)
    else:
        end_x, end_y = grid.end_xy

        def heuristic(node_id):
            """Euklidische Distanz zum Ziel als Unterschätzung des Restweges."""
            x, y = grid.node_xy(node_id)
            return math.hypot(x - end_x, y - end_y)

        node_path, route_length_m = _astar(neighbors, heuristic, grid.start_id, grid.end_id)

    # --- Route als Geometrie und Karte erzeugen (einzige Ausgabedatei) ---

    route_line, route_points, start_point, end_point = _build_route_geometries(grid, node_path)

    map_file = _visualize_route_png(
        grid=grid,
        zones=zones,
        route_line=route_line,
        route_points=route_points,
        start_point=start_point,
        end_point=end_point,
        output_png=output_png,
        show_map=show_map,
        satellite_background=satellite_background,
        basemap_zoom=basemap_zoom,
        fixed_extent=fixed_extent,
        center_lat=center_lat,
        center_lon=center_lon,
        square_side_km=square_side_km,
    )

    total_graph_length_m = grid.total_edge_length_m

    return {
        "algorithm":             algorithm,
        "start_node_id":         grid.start_id,
        "end_node_id":           grid.end_id,
        "route_length_m":        route_length_m,
        "route_length_km":       route_length_m / 1000,
        "total_graph_length_m":  total_graph_length_m,
        "total_graph_length_km": total_graph_length_m / 1000,
        "route_node_count":      len(node_path),
        "route_edge_count":      len(node_path) - 1,
        "route_map_png":         map_file,
    }
