"""
Wegfindungs.py – Kürzeste-Weg-Suche im Navigationsgraphen.

Liest den erzeugten Graphen (Knoten + Kanten als GeoJSON) ein,
sucht den kürzesten Weg zwischen Start- und Endknoten mit Dijkstra oder A*
und visualisiert das Ergebnis als PNG-Karte.
"""

from pathlib import Path
from heapq import heappush, heappop
from math import sqrt

from utils import (
    WGS84, WEB_MERCATOR, DEFAULT_METRIC_CRS,
    read_geojson, get_fixed_extent_web_mercator,
    setup_map_figure, save_map_figure,
)


# =============================================================================
# Graph-Aufbau
# =============================================================================

def _find_start_end_node_ids(nodes_gdf):
    """
    Liest die node_id von Start- und Endknoten aus dem GeoDataFrame.

    Wirft ValueError, wenn die Spalte 'node_kind' fehlt oder kein
    Start- bzw. Endknoten vorhanden ist.
    """
    if "node_kind" not in nodes_gdf.columns:
        raise ValueError("In graph_nodes.geojson fehlt die Spalte 'node_kind'.")

    start_nodes = nodes_gdf[nodes_gdf["node_kind"] == "start"]
    end_nodes   = nodes_gdf[nodes_gdf["node_kind"] == "end"]

    if start_nodes.empty:
        raise ValueError("Kein Startknoten gefunden. Erwartet: node_kind == 'start'.")

    if end_nodes.empty:
        raise ValueError("Kein Endknoten gefunden. Erwartet: node_kind == 'end'.")

    return int(start_nodes.iloc[0]["node_id"]), int(end_nodes.iloc[0]["node_id"])


def _build_adjacency(edges_gdf, *, directed=False):
    """
    Erstellt eine Adjazenzliste aus den Kanten des Graphen.

    directed:
        False – Kanten gelten in beide Richtungen (Standardfall).
        True  – Kanten gelten nur von from_node nach to_node.

    Rückgabe:
        Dict: node_id -> Liste von {to_node, edge_id, length_m}.
    """
    required_columns = {"from_node", "to_node", "length_m", "edge_id"}
    missing = required_columns - set(edges_gdf.columns)

    if missing:
        raise ValueError(f"In graph_edges.geojson fehlen Spalten: {missing}")

    adjacency = {}

    for _, row in edges_gdf.iterrows():
        from_node = int(row["from_node"])
        to_node   = int(row["to_node"])
        edge_id   = int(row["edge_id"])
        length_m  = float(row["length_m"])

        adjacency.setdefault(from_node, []).append(
            {"to_node": to_node, "edge_id": edge_id, "length_m": length_m}
        )

        # Bei ungerichtetem Graph Rückkante eintragen.
        if not directed:
            adjacency.setdefault(to_node, []).append(
                {"to_node": from_node, "edge_id": edge_id, "length_m": length_m}
            )

    return adjacency


# =============================================================================
# Pfad-Rekonstruktion
# =============================================================================

def _reconstruct_path(previous, start_node_id, end_node_id):
    """
    Rekonstruiert Knoten- und Kantenpfad aus dem previous-Dict des Suchalgorithmus.

    Wirft ValueError, wenn kein Weg existiert.
    """
    if end_node_id not in previous and start_node_id != end_node_id:
        raise ValueError("Kein Weg zwischen Start und Ende gefunden.")

    node_path = [end_node_id]
    edge_path = []
    current   = end_node_id

    # Rückwärts vom Ziel zum Start traversieren.
    while current != start_node_id:
        prev_node, edge_id = previous[current]
        edge_path.append(edge_id)
        node_path.append(prev_node)
        current = prev_node

    node_path.reverse()
    edge_path.reverse()

    return node_path, edge_path


# =============================================================================
# Suchalgorithmen
# =============================================================================

def _dijkstra(adjacency, start_node_id, end_node_id):
    """
    Dijkstra-Algorithmus zur Suche des kürzesten Weges.

    Verwendet einen Min-Heap für effiziente Extraktion des nächstgelegenen Knotens.

    Rückgabe:
        (node_path, edge_path, route_length_m)
    """
    distances = {start_node_id: 0.0}
    previous  = {}
    queue     = [(0.0, start_node_id)]
    visited   = set()

    while queue:
        current_distance, current_node = heappop(queue)

        if current_node in visited:
            continue

        visited.add(current_node)

        if current_node == end_node_id:
            break

        for edge in adjacency.get(current_node, []):
            neighbor     = edge["to_node"]
            new_distance = current_distance + edge["length_m"]

            if new_distance < distances.get(neighbor, float("inf")):
                distances[neighbor]  = new_distance
                previous[neighbor]   = (current_node, edge["edge_id"])
                heappush(queue, (new_distance, neighbor))

    if end_node_id not in distances:
        raise ValueError("Dijkstra konnte keinen Weg finden.")

    node_path, edge_path = _reconstruct_path(previous, start_node_id, end_node_id)
    return node_path, edge_path, distances[end_node_id]


def _astar(adjacency, nodes_metric, start_node_id, end_node_id):
    """
    A*-Algorithmus zur Suche des kürzesten Weges.

    Nutzt die euklidische Luftlinienentfernung als zulässige Heuristik,
    die den tatsächlichen Restweg nie überschätzt.

    Rückgabe:
        (node_path, edge_path, route_length_m)
    """
    nodes_by_id  = nodes_metric.set_index("node_id_int")
    target_point = nodes_by_id.loc[end_node_id].geometry

    def heuristic(node_id):
        """Euklidische Distanz vom Knoten zum Ziel als Unterschätzung des Restweges."""
        point = nodes_by_id.loc[node_id].geometry
        dx    = point.x - target_point.x
        dy    = point.y - target_point.y
        return sqrt(dx * dx + dy * dy)

    g_score  = {start_node_id: 0.0}
    previous = {}
    queue    = [(heuristic(start_node_id), 0.0, start_node_id)]
    visited  = set()

    while queue:
        _, current_g, current_node = heappop(queue)

        if current_node in visited:
            continue

        visited.add(current_node)

        if current_node == end_node_id:
            break

        for edge in adjacency.get(current_node, []):
            neighbor    = edge["to_node"]
            tentative_g = current_g + edge["length_m"]

            if tentative_g < g_score.get(neighbor, float("inf")):
                g_score[neighbor]  = tentative_g
                previous[neighbor] = (current_node, edge["edge_id"])
                f_score            = tentative_g + heuristic(neighbor)
                heappush(queue, (f_score, tentative_g, neighbor))

    if end_node_id not in g_score:
        raise ValueError("A* konnte keinen Weg finden.")

    node_path, edge_path = _reconstruct_path(previous, start_node_id, end_node_id)
    return node_path, edge_path, g_score[end_node_id]


# =============================================================================
# Route als GeoJSON speichern
# =============================================================================

def _create_route_geojsons(
    *,
    nodes_metric,
    edges_metric,
    node_path,
    edge_path,
    output_route_nodes_geojson,
    output_route_edges_geojson,
):
    """
    Extrahiert die Route aus dem Graphen und speichert sie als GeoJSON.

    Rückgabe:
        (route_nodes_wgs84, route_edges_wgs84) – beide als GeoDataFrames in WGS84.
    """
    node_indexed = nodes_metric.set_index("node_id_int")

    # Knoten in Reihenfolge der Route extrahieren.
    route_nodes               = node_indexed.loc[node_path].copy()
    route_nodes["route_order"] = range(len(route_nodes))

    # Kanten nach edge_id filtern und in Reihenfolge sortieren.
    edge_order_by_id           = {int(eid): order for order, eid in enumerate(edge_path)}
    edges_metric               = edges_metric.copy()
    edges_metric["edge_id_int"] = edges_metric["edge_id"].astype(int)

    route_edges               = edges_metric[edges_metric["edge_id_int"].isin(edge_order_by_id)].copy()
    route_edges["route_order"] = route_edges["edge_id_int"].map(edge_order_by_id)
    route_edges               = route_edges.sort_values("route_order")

    route_nodes_wgs84 = route_nodes.to_crs(WGS84)
    route_edges_wgs84 = route_edges.to_crs(WGS84)

    output_route_nodes_geojson = Path(output_route_nodes_geojson)
    output_route_edges_geojson = Path(output_route_edges_geojson)

    output_route_nodes_geojson.parent.mkdir(parents=True, exist_ok=True)
    output_route_edges_geojson.parent.mkdir(parents=True, exist_ok=True)

    route_nodes_wgs84.to_file(output_route_nodes_geojson, driver="GeoJSON")
    route_edges_wgs84.to_file(output_route_edges_geojson, driver="GeoJSON")

    return route_nodes_wgs84, route_edges_wgs84


# =============================================================================
# Visualisierung als PNG
# =============================================================================

def _visualize_route_png(
    *,
    nodes_geojson,
    edges_geojson,
    route_nodes_geojson,
    route_edges_geojson,
    zones_geojson,
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
        5. Route-Kanten – magenta, breit
        6. Route-Knoten – weiß mit schwarzem Rand
        7. Start- und Endpunkt – grün / rot, groß

    Rückgabe:
        Path-Objekt der gespeicherten PNG-Datei.
    """

    # Alle Layer einlesen und in Web Mercator projizieren.
    nodes       = read_geojson(nodes_geojson).to_crs(WEB_MERCATOR)
    edges       = read_geojson(edges_geojson).to_crs(WEB_MERCATOR)
    route_nodes = read_geojson(route_nodes_geojson).to_crs(WEB_MERCATOR)
    route_edges = read_geojson(route_edges_geojson).to_crs(WEB_MERCATOR)

    zones = None
    if zones_geojson is not None:
        zones = read_geojson(zones_geojson).to_crs(WEB_MERCATOR)

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

    # Ebene 5: Route-Kanten
    route_edges.plot(ax=ax, color="magenta", linewidth=3.0, alpha=0.95, zorder=5)

    # Ebene 6: Route-Knoten
    route_nodes.plot(ax=ax, color="white", edgecolor="black", markersize=18, alpha=1.0, zorder=6)

    # Ebene 7: Start- und Endpunkt hervorheben.
    if "node_kind" in route_nodes.columns:
        start = route_nodes[route_nodes["node_kind"] == "start"]
        end   = route_nodes[route_nodes["node_kind"] == "end"]

        if not start.empty:
            start.plot(ax=ax, color="lime", edgecolor="black", markersize=80, zorder=7)

        if not end.empty:
            end.plot(ax=ax, color="red", edgecolor="black", markersize=80, zorder=7)

    return save_map_figure(fig, ax, output_png, title="Kürzester Weg im Graphen", show_map=show_map)


# =============================================================================
# Haupt-Pipeline-Funktion
# =============================================================================

def find_path_and_visualize(
    nodes_geojson,
    edges_geojson,
    *,
    zones_geojson=None,
    output_route_nodes_geojson="route_nodes.geojson",
    output_route_edges_geojson="route_edges.geojson",
    output_png="route_karte.png",
    algorithm="dijkstra",
    directed=False,
    metric_crs=DEFAULT_METRIC_CRS,
    show_map=True,
    satellite_background=True,
    basemap_zoom=13,
    fixed_extent=True,
    center_lat=48.137154,
    center_lon=11.576124,
    square_side_km=25,
):
    """
    Sucht den kürzesten Weg im Navigationsgraphen und visualisiert ihn.

    algorithm:
        "dijkstra" – Dijkstra-Algorithmus (optimal, keine Heuristik).
        "astar"    – A*-Algorithmus (schneller durch euklidische Heuristik).
    directed:
        False – Kanten gelten in beide Richtungen.
        True  – Kanten gelten nur in Richtung from_node -> to_node.

    Rückgabe:
        Dict mit Ergebniskennzahlen (Algorithmus, Länge, Laufzeit, Dateipfade).
    """
    algorithm = algorithm.lower().strip()

    if algorithm not in {"dijkstra", "astar"}:
        raise ValueError("algorithm muss 'dijkstra' oder 'astar' sein.")

    # --- Graph einlesen ---

    nodes = read_geojson(nodes_geojson)
    edges = read_geojson(edges_geojson)

    nodes_metric = nodes.to_crs(metric_crs)
    edges_metric = edges.to_crs(metric_crs)

    # Integer-Index für A*-Heuristik und Routenextraktion.
    nodes_metric["node_id_int"] = nodes_metric["node_id"].astype(int)

    start_node_id, end_node_id = _find_start_end_node_ids(nodes_metric)

    adjacency = _build_adjacency(edges_metric, directed=directed)

    # --- Wegsuche ---

    if algorithm == "dijkstra":
        node_path, edge_path, route_length_m = _dijkstra(
            adjacency=adjacency,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
        )
    else:
        node_path, edge_path, route_length_m = _astar(
            adjacency=adjacency,
            nodes_metric=nodes_metric,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
        )

    # --- Route als GeoJSON speichern ---

    _create_route_geojsons(
        nodes_metric=nodes_metric,
        edges_metric=edges_metric,
        node_path=node_path,
        edge_path=edge_path,
        output_route_nodes_geojson=output_route_nodes_geojson,
        output_route_edges_geojson=output_route_edges_geojson,
    )

    total_graph_length_m = float(edges_metric["length_m"].astype(float).sum())

    # --- Karte erzeugen ---

    map_file = _visualize_route_png(
        nodes_geojson=nodes_geojson,
        edges_geojson=edges_geojson,
        route_nodes_geojson=output_route_nodes_geojson,
        route_edges_geojson=output_route_edges_geojson,
        zones_geojson=zones_geojson,
        output_png=output_png,
        show_map=show_map,
        satellite_background=satellite_background,
        basemap_zoom=basemap_zoom,
        fixed_extent=fixed_extent,
        center_lat=center_lat,
        center_lon=center_lon,
        square_side_km=square_side_km,
    )

    # --- Ergebnis zusammenstellen und ausgeben ---

    result = {
        "algorithm":             algorithm,
        "start_node_id":         start_node_id,
        "end_node_id":           end_node_id,
        "route_length_m":        route_length_m,
        "route_length_km":       route_length_m / 1000,
        "total_graph_length_m":  total_graph_length_m,
        "total_graph_length_km": total_graph_length_m / 1000,
        "route_node_count":      len(node_path),
        "route_edge_count":      len(edge_path),
        "route_nodes_geojson":   Path(output_route_nodes_geojson),
        "route_edges_geojson":   Path(output_route_edges_geojson),
        "route_map_png":         map_file,
    }

    print("Wegsuche abgeschlossen.")
    print(f"Algorithmus:                    {result['algorithm']}")
    print(f"Start-Knoten:                   {result['start_node_id']}")
    print(f"End-Knoten:                     {result['end_node_id']}")
    print(f"Weglänge:                       {result['route_length_m']:.2f} m")
    print(f"Weglänge:                       {result['route_length_km']:.3f} km")
    print(f"Gesamtlänge aller Graph-Kanten: {result['total_graph_length_km']:.3f} km")
    print(f"Route-Knoten:                   {result['route_node_count']}")
    print(f"Route-Kanten:                   {result['route_edge_count']}")
    print(f"Route-Nodes-Datei:              {result['route_nodes_geojson']}")
    print(f"Route-Edges-Datei:              {result['route_edges_geojson']}")
    print(f"Route-Karte:                    {result['route_map_png']}")

    return result
