from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, LineString


WGS84 = "EPSG:4326"
DEFAULT_METRIC_CRS = "EPSG:25832"  # Für München sinnvoll


def _get_geometry_union(gdf):
    """
    Vereinigt alle Geometrien zu einer einzigen Geometrie.
    Kompatibel mit älteren und neueren GeoPandas-Versionen.
    """

    try:
        return gdf.geometry.union_all()
    except AttributeError:
        return gdf.geometry.unary_union


def _wgs84_point_to_metric(lat, lon, metric_crs):
    """
    Wandelt einen WGS84-Punkt in das metrische Projektionssystem um.
    """

    point_wgs84 = gpd.GeoSeries(
        [Point(lon, lat)],
        crs=WGS84,
    )

    return point_wgs84.to_crs(metric_crs).iloc[0]


def _edge_orientation(from_point, to_point, tolerance=0.001):
    """
    Bestimmt die Orientierung einer Kante im metrischen CRS.

    Rückgabe:
        "west_ost"
        "nord_sued"
        "diagonal"
        "special"
    """

    dx = abs(to_point.x - from_point.x)
    dy = abs(to_point.y - from_point.y)

    if dy <= tolerance and dx > tolerance:
        return "west_ost"

    if dx <= tolerance and dy > tolerance:
        return "nord_sued"

    if dx > tolerance and dy > tolerance:
        return "diagonal"

    return "special"


def _connect_special_node_to_grid(
    *,
    special_node,
    grid_nodes,
    forbidden_area,
    edges,
    start_edge_id,
    edge_kind,
    max_connections,
):
    """
    Verbindet Start- oder Endpunkt mit den nächsten sichtbaren Gitterknoten.

    Die Verbindung darf keine Sperrzone schneiden.
    Diese Verbindungen dürfen schräg sein, auch wenn diagonale Gitterkanten
    deaktiviert sind, weil Start-/Endpunkt nicht exakt auf dem Raster liegen.
    """

    if max_connections < 1:
        raise ValueError("special_connections_per_point muss mindestens 1 sein.")

    special_point = special_node["geometry"]

    candidates = []

    for grid_node in grid_nodes:
        grid_point = grid_node["geometry"]
        distance_m = special_point.distance(grid_point)

        if distance_m <= 0:
            continue

        candidates.append((distance_m, grid_node))

    candidates.sort(key=lambda item: item[0])

    edge_id = start_edge_id
    created_connections = 0

    for distance_m, grid_node in candidates:
        grid_point = grid_node["geometry"]

        line = LineString([special_point, grid_point])

        # Verbindung darf nicht durch oder über eine Sperrzone laufen
        if line.intersects(forbidden_area):
            continue

        orientation = _edge_orientation(special_point, grid_point)

        edges.append({
            "edge_id": edge_id,
            "from_node": special_node["node_id"],
            "to_node": grid_node["node_id"],
            "length_m": line.length,
            "spacing_m": None,
            "diagonal": orientation == "diagonal",
            "connect_diagonal": None,
            "edge_kind": edge_kind,
            "orientation": orientation,
            "geometry": line,
        })

        edge_id += 1
        created_connections += 1

        if created_connections >= max_connections:
            break

    if created_connections == 0:
        raise ValueError(
            f"Für {special_node['node_kind']} konnte kein sichtbarer Gitterknoten "
            f"ohne Schnitt durch eine Sperrzone gefunden werden."
        )

    return edge_id


def create_navigation_graph(
    zones_geojson,
    output_nodes_geojson,
    output_edges_geojson,
    *,
    spacing_m=250,
    metric_crs=DEFAULT_METRIC_CRS,
    connect_diagonal=False,
    bbox_padding_m=0,

    start_lat=None,
    start_lon=None,
    end_lat=None,
    end_lon=None,
    special_connections_per_point=1,
):
    """
    Erstellt einen Graphen aus regelmäßig verteilten Punkten.

    Regeln:
        - Gitterknoten liegen im Abstand spacing_m.
        - Gitterknoten innerhalb oder auf Sperrzonen werden entfernt.
        - Gitterkanten, die durch oder über Sperrzonen laufen, werden entfernt.
        - Wenn connect_diagonal=False:
            Gitterkanten sind exakt West-Ost oder Nord-Süd orientiert.
        - Wenn connect_diagonal=True:
            Zusätzlich direkte diagonale Nachbarn.
        - Start- und Endpunkt werden als zusätzliche Knoten eingefügt.
        - Start- und Endpunkt werden mit den nächsten sichtbaren Gitterknoten
          verbunden, ohne Sperrzonen zu schneiden.
        - Diese Start-/End-Verbindungen dürfen schräg sein, auch wenn
          diagonale Gitterverbindungen deaktiviert sind.

    Ausgabe:
        - Nodes als GeoJSON
        - Edges als GeoJSON
    """

    zones_path = Path(zones_geojson)
    nodes_path = Path(output_nodes_geojson)
    edges_path = Path(output_edges_geojson)

    if not zones_path.exists():
        raise FileNotFoundError(f"Zonen-Datei nicht gefunden: {zones_path}")

    if spacing_m <= 0:
        raise ValueError("spacing_m muss größer als 0 sein.")

    if bbox_padding_m < 0:
        raise ValueError("bbox_padding_m darf nicht negativ sein.")

    if special_connections_per_point < 1:
        raise ValueError("special_connections_per_point muss mindestens 1 sein.")

    zones = gpd.read_file(zones_path)

    if zones.empty:
        raise ValueError("Die Zonen-Datei enthält keine Features.")

    if zones.crs is None:
        zones = zones.set_crs(WGS84)
    else:
        zones = zones.to_crs(WGS84)

    # In metrisches CRS umwandeln
    zones_metric = zones.to_crs(metric_crs)

    # Geometrien reparieren
    zones_metric["geometry"] = zones_metric.geometry.buffer(0)

    # Alle Sperrzonen zu einer Gesamtfläche vereinigen
    forbidden_area = _get_geometry_union(zones_metric)

    # Start-/Endpunkte vorbereiten
    special_points = []

    if start_lat is not None and start_lon is not None:
        start_point_metric = _wgs84_point_to_metric(
            lat=start_lat,
            lon=start_lon,
            metric_crs=metric_crs,
        )
        special_points.append({
            "node_kind": "start",
            "label": "Start",
            "geometry": start_point_metric,
        })

    if end_lat is not None and end_lon is not None:
        end_point_metric = _wgs84_point_to_metric(
            lat=end_lat,
            lon=end_lon,
            metric_crs=metric_crs,
        )
        special_points.append({
            "node_kind": "end",
            "label": "Ende",
            "geometry": end_point_metric,
        })

    for special_point in special_points:
        if special_point["geometry"].intersects(forbidden_area):
            raise ValueError(
                f"{special_point['label']} liegt innerhalb oder auf einer Sperrzone."
            )

    # Bounding Box der Zonen bestimmen
    minx, miny, maxx, maxy = zones_metric.total_bounds

    # Bounding Box zusätzlich auf Start-/Endpunkte erweitern
    for special_point in special_points:
        point = special_point["geometry"]
        minx = min(minx, point.x)
        miny = min(miny, point.y)
        maxx = max(maxx, point.x)
        maxy = max(maxy, point.y)

    minx -= bbox_padding_m
    miny -= bbox_padding_m
    maxx += bbox_padding_m
    maxy += bbox_padding_m

    # -------------------------------------------------
    # 1. Gitterknoten erzeugen
    # -------------------------------------------------

    grid_nodes = []
    node_lookup = {}

    row_index = 0
    y = miny

    while y <= maxy:
        col_index = 0
        x = minx

        while x <= maxx:
            point = Point(x, y)

            # Punkt darf nicht in oder auf einer Sperrzone liegen
            if not point.intersects(forbidden_area):
                node_id = len(grid_nodes)

                grid_node = {
                    "node_id": node_id,
                    "node_kind": "grid",
                    "label": None,
                    "grid_row": row_index,
                    "grid_col": col_index,
                    "x": x,
                    "y": y,
                    "spacing_m": spacing_m,
                    "geometry": point,
                }

                grid_nodes.append(grid_node)
                node_lookup[(row_index, col_index)] = node_id

            x += spacing_m
            col_index += 1

        y += spacing_m
        row_index += 1

    if not grid_nodes:
        raise ValueError("Es wurden keine erlaubten Gitterknoten erzeugt.")

    nodes = list(grid_nodes)

    # -------------------------------------------------
    # 2. Start-/Endknoten hinzufügen
    # -------------------------------------------------

    for special_point in special_points:
        node_id = len(nodes)
        point = special_point["geometry"]

        nodes.append({
            "node_id": node_id,
            "node_kind": special_point["node_kind"],
            "label": special_point["label"],
            "grid_row": None,
            "grid_col": None,
            "x": point.x,
            "y": point.y,
            "spacing_m": None,
            "geometry": point,
        })

    # -------------------------------------------------
    # 3. Gitterkanten erzeugen
    # -------------------------------------------------

    # Ohne Diagonalen:
    #   Nur rechts und nächste Reihe gleiche Spalte.
    #   Dadurch entstehen exakt West-Ost- bzw. Nord-Süd-Kanten.
    #
    # Mit Diagonalen:
    #   Zusätzlich direkte diagonale Nachbarn.
    #
    # Es werden nur Vorwärts-Nachbarn geprüft, damit keine doppelten Kanten entstehen.
    if connect_diagonal:
        neighbor_offsets = [
            (0, 1),    # West-Ost
            (1, 0),    # Nord-Süd
            (1, 1),    # diagonal
            (1, -1),   # diagonal
        ]
    else:
        neighbor_offsets = [
            (0, 1),    # West-Ost
            (1, 0),    # Nord-Süd
        ]

    node_by_id = {
        node["node_id"]: node
        for node in nodes
    }

    edges = []
    edge_id = 0

    for node in grid_nodes:
        from_id = node["node_id"]
        row = node["grid_row"]
        col = node["grid_col"]
        from_point = node["geometry"]

        for d_row, d_col in neighbor_offsets:
            neighbor_key = (row + d_row, col + d_col)

            if neighbor_key not in node_lookup:
                continue

            to_id = node_lookup[neighbor_key]
            to_point = node_by_id[to_id]["geometry"]

            line = LineString([from_point, to_point])

            # Verbindung darf nicht durch oder über eine Sperrzone laufen
            if line.intersects(forbidden_area):
                continue

            orientation = _edge_orientation(from_point, to_point)

            edges.append({
                "edge_id": edge_id,
                "from_node": from_id,
                "to_node": to_id,
                "length_m": line.length,
                "spacing_m": spacing_m,
                "diagonal": orientation == "diagonal",
                "connect_diagonal": connect_diagonal,
                "edge_kind": "grid",
                "orientation": orientation,
                "geometry": line,
            })

            edge_id += 1

    # -------------------------------------------------
    # 4. Start-/Endpunkt mit nächstem sichtbaren Gitter verbinden
    # -------------------------------------------------

    special_nodes = [
        node
        for node in nodes
        if node["node_kind"] in {"start", "end"}
    ]

    for special_node in special_nodes:
        edge_kind = f"{special_node['node_kind']}_connection"

        edge_id = _connect_special_node_to_grid(
            special_node=special_node,
            grid_nodes=grid_nodes,
            forbidden_area=forbidden_area,
            edges=edges,
            start_edge_id=edge_id,
            edge_kind=edge_kind,
            max_connections=special_connections_per_point,
        )

    if not edges:
        raise ValueError("Es wurden keine erlaubten Kanten erzeugt.")

    nodes_gdf = gpd.GeoDataFrame(
        nodes,
        geometry="geometry",
        crs=metric_crs,
    )

    edges_gdf = gpd.GeoDataFrame(
        edges,
        geometry="geometry",
        crs=metric_crs,
    )

    # -------------------------------------------------
    # 5. Als GeoJSON speichern
    # -------------------------------------------------

    nodes_path.parent.mkdir(parents=True, exist_ok=True)
    edges_path.parent.mkdir(parents=True, exist_ok=True)

    nodes_wgs84 = nodes_gdf.to_crs(WGS84)
    edges_wgs84 = edges_gdf.to_crs(WGS84)

    nodes_wgs84.to_file(nodes_path, driver="GeoJSON")
    edges_wgs84.to_file(edges_path, driver="GeoJSON")

    return nodes_wgs84, edges_wgs84


def main():
    zones_geojson = "drohnen_luftvo_zonen.geojson"
    output_nodes = "graph_nodes.geojson"
    output_edges = "graph_edges.geojson"

    nodes, edges = create_navigation_graph(
        zones_geojson=zones_geojson,
        output_nodes_geojson=output_nodes,
        output_edges_geojson=output_edges,

        spacing_m=250,
        connect_diagonal=False,
        bbox_padding_m=0,

        start_lat=48.14018493850112,
        start_lon=11.56075451365665,
        end_lat=48.07276903757395,
        end_lon=11.637402988925738,
        special_connections_per_point=1,
    )

    print("Graph wurde erzeugt.")
    print(f"Knoten: {len(nodes)}")
    print(f"Kanten: {len(edges)}")
    print(f"Nodes-Datei: {output_nodes}")
    print(f"Edges-Datei: {output_edges}")


if __name__ == "__main__":
    main()