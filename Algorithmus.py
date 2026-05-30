"""
Algorithmus.py – Kürzeste-Weg-Suche auf dem Matrix-Gitter.

Erhält das NavigationGrid (Matrix-Darstellung) im Speicher und sucht den
kürzesten Weg zwischen Start- und Endknoten mit Dijkstra, A* oder
Floyd-Warshall.

Die Algorithmen arbeiten direkt auf den (row, col)-Indizes der Matrix:
Die Nachbarn eines Knotens werden über die Passierbarkeits-Arrays bestimmt –
es wird keine Adjazenzliste aufgebaut. Start- und Endknoten liegen außerhalb
des Gitters und sind über ihre vorab gespeicherten Verbindungen angebunden.

Die Darstellung der Route ist in Visualisierung.py ausgelagert; dieses Modul
liefert nur die Knotenfolge und ruft die Visualisierung zum Schluss auf.
"""

import math
from heapq import heappush, heappop

import numpy as np

from Visualisierung import build_route_geometries, visualize_route_png


# Floyd-Warshall benötigt eine volle (V x V)-Distanzmatrix. Oberhalb dieser
# Knotenzahl ist der Speicher- und Rechenaufwand (O(V^2) Speicher, O(V^3) Zeit)
# nicht mehr praktikabel; die Suche bricht dann mit einer klaren Meldung ab.
FLOYD_WARSHALL_MAX_NODES = 4000


# =============================================================================
# Nachbarschaft auf der Matrix
# =============================================================================

def _make_neighbor_function(grid):
    """
    Erstellt eine Funktion, die zu einem Knoten seine Nachbarn liefert.

    Für Gitterknoten werden die Nachbarn über die Passierbarkeits-Arrays der
    Matrix bestimmt (4 orthogonale Richtungen). Start- und Endknoten nutzen
    ihre gespeicherten Verbindungen.

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


def _floyd_warshall(grid, neighbors, start_id, end_id):
    """
    Floyd-Warshall-Algorithmus (alle kürzesten Wege zwischen allen Knoten).

    Anders als Dijkstra/A* berechnet Floyd-Warshall die kürzesten Distanzen
    zwischen ALLEN Knotenpaaren. Das ist hier eigentlich mehr als nötig, dient
    aber als vollständige, vergleichbare Variante. Aus dem Ergebnis wird der
    Weg von Start zu Ende extrahiert.

    Aufwand: O(V^2) Speicher und O(V^3) Zeit. Für feine Gitter daher nur bis
    FLOYD_WARSHALL_MAX_NODES Knoten praktikabel.

    Rückgabe:
        (node_path, route_length_m)
    """
    # --- Alle erreichbaren Knoten aufzählen (Gitterknoten + Start + Ende) ---
    valid_rows, valid_cols = np.nonzero(grid.node_valid)
    node_ids = [int(r) * grid.n_cols + int(c) for r, c in zip(valid_rows, valid_cols)]
    node_ids.append(start_id)
    node_ids.append(end_id)

    n = len(node_ids)

    if n > FLOYD_WARSHALL_MAX_NODES:
        raise ValueError(
            f"Floyd-Warshall ist für {n} Knoten nicht praktikabel "
            f"(Grenze: {FLOYD_WARSHALL_MAX_NODES}). Der Aufwand wächst mit "
            f"O(V^2) Speicher und O(V^3) Zeit. Bitte eine gröbere Auflösung "
            f"(NETZ_AUFLOESUNG_M) oder einen kleineren Ausschnitt wählen, oder "
            f"Dijkstra/A* verwenden."
        )

    index_of = {node_id: i for i, node_id in enumerate(node_ids)}

    # --- Distanz- und Nachfolger-Matrix initialisieren ---
    dist = np.full((n, n), np.inf, dtype=np.float64)
    nxt  = np.full((n, n), -1, dtype=np.int64)

    np.fill_diagonal(dist, 0.0)
    for i in range(n):
        nxt[i, i] = i

    # Direkte Kanten aus der Nachbarschaft eintragen.
    for i, node_id in enumerate(node_ids):
        for neighbor, cost in neighbors(node_id):
            j = index_of[neighbor]
            if cost < dist[i, j]:
                dist[i, j] = cost
                nxt[i, j]  = j

    # --- Kern: für jeden Zwischenknoten k alle Paare (i, j) verbessern ---
    # Vektorisiert über numpy: pro k ein O(V^2)-Schritt statt zweier Schleifen.
    for k in range(n):
        through_k = dist[:, k, None] + dist[None, k, :]
        improved  = through_k < dist
        dist      = np.where(improved, through_k, dist)
        nxt       = np.where(improved, nxt[:, k, None], nxt)

    s = index_of[start_id]
    e = index_of[end_id]

    # --- Weg aus der Nachfolger-Matrix rekonstruieren ---
    path    = [start_id]
    current = s
    while current != e:
        current = int(nxt[current, e])
        path.append(node_ids[current])

    return path, float(dist[s, e])


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
        "dijkstra"        – Dijkstra-Algorithmus (optimal, keine Heuristik).
        "astar"           – A*-Algorithmus (schneller durch euklidische Heuristik).
        "floyd_warshall"  – Floyd-Warshall (alle Paare; nur für kleine Gitter).

    Rückgabe:
        Dict mit Ergebniskennzahlen (Algorithmus, Länge, Knotenzahl, PNG-Pfad).
    """
    neighbors = _make_neighbor_function(grid)

    # --- Wegsuche direkt auf der Matrix ---

    if algorithm == "dijkstra":
        node_path, route_length_m = _dijkstra(neighbors, grid.start_id, grid.end_id)
    elif algorithm == "floyd_warshall":
        node_path, route_length_m = _floyd_warshall(grid, neighbors, grid.start_id, grid.end_id)
    elif algorithm == "astar":
        end_x, end_y = grid.end_xy

        def heuristic(node_id):
            """Euklidische Distanz zum Ziel als Unterschätzung des Restweges."""
            x, y = grid.node_xy(node_id)
            return math.hypot(x - end_x, y - end_y)

        node_path, route_length_m = _astar(neighbors, heuristic, grid.start_id, grid.end_id)
    else:
        raise ValueError(
            f"Unbekannter Algorithmus '{algorithm}'. "
            f"Erlaubt: 'dijkstra', 'astar', 'floyd_warshall'."
        )

    # --- Route als Geometrie und Karte erzeugen (einzige Ausgabedatei) ---

    route_line, route_points, start_point, end_point = build_route_geometries(grid, node_path)

    map_file = visualize_route_png(
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
