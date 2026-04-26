from pathlib import Path
from math import sqrt

import geopandas as gpd
from shapely.geometry import Point, LineString


WGS84 = "EPSG:4326"
DEFAULT_METRIC_CRS = "EPSG:25832"  # Für München sinnvoll


def create_navigation_graph(
    zones_geojson,
    output_nodes_geojson,
    output_edges_geojson,
    *,
    spacing_m=250,
    metric_crs=DEFAULT_METRIC_CRS,
    connect_diagonal=True,
    bbox_padding_m=0,
):
    """
    Erstellt einen Graphen aus regelmäßig verteilten Punkten.

    Regeln:
        - Knoten liegen im Abstand spacing_m.
        - Knoten innerhalb von Zonen werden entfernt.
        - Kanten, die durch Zonen laufen, werden entfernt.
        - Ausgabe erfolgt als zwei GeoJSON-Dateien:
            1. Nodes/Punkte
            2. Edges/Verbindungen

    Parameter:
        zones_geojson:
            GeoJSON mit den LuftVO-Zonen.

        output_nodes_geojson:
            Ausgabe-Datei für erlaubte Knoten.

        output_edges_geojson:
            Ausgabe-Datei für erlaubte Kanten.

        spacing_m:
            Abstand zwischen Knoten in Metern.

        connect_diagonal:
            True = 8er-Nachbarschaft.
            False = nur horizontal/vertikal.

        bbox_padding_m:
            Erweiterung der Bounding Box nach außen.
    """

    zones_path = Path(zones_geojson)
    nodes_path = Path(output_nodes_geojson)
    edges_path = Path(output_edges_geojson)

    if not zones_path.exists():
        raise FileNotFoundError(f"Zonen-Datei nicht gefunden: {zones_path}")

    if spacing_m <= 0:
        raise ValueError("spacing_m muss größer als 0 sein.")

    zones = gpd.read_file(zones_path)

    if zones.empty:
        raise ValueError("Die Zonen-Datei enthält keine Features.")

    if zones.crs is None:
        zones = zones.set_crs(WGS84)
    else:
        zones = zones.to_crs(WGS84)

    zones_metric = zones.to_crs(metric_crs)

    # Geometrien reparieren
    zones_metric["geometry"] = zones_metric.geometry.buffer(0)

    # Alle Zonen zu einer Geometrie vereinigen
    forbidden_area = zones_metric.union_all()

    minx, miny, maxx, maxy = zones_metric.total_bounds

    minx -= bbox_padding_m
    miny -= bbox_padding_m
    maxx += bbox_padding_m
    maxy += bbox_padding_m

    # -------------------------------------------------
    # 1. Knoten erzeugen
    # -------------------------------------------------

    nodes = []
    node_lookup = {}

    row_index = 0
    y = miny

    while y <= maxy:
        col_index = 0
        x = minx

        while x <= maxx:
            point = Point(x, y)

            # Punkt darf nicht in oder auf einer Zone liegen
            if not point.intersects(forbidden_area):
                node_id = len(nodes)

                nodes.append({
                    "node_id": node_id,
                    "grid_row": row_index,
                    "grid_col": col_index,
                    "x": x,
                    "y": y,
                    "geometry": point,
                })

                node_lookup[(row_index, col_index)] = node_id

            x += spacing_m
            col_index += 1

        y += spacing_m
        row_index += 1

    if not nodes:
        raise ValueError("Es wurden keine erlaubten Knoten erzeugt.")

    nodes_gdf = gpd.GeoDataFrame(
        nodes,
        geometry="geometry",
        crs=metric_crs,
    )

    # -------------------------------------------------
    # 2. Kanten erzeugen
    # -------------------------------------------------

    if connect_diagonal:
        neighbor_offsets = [
            (0, 1),    # rechts
            (1, 0),    # oben/unten je nach Koordinatensystem
            (1, 1),    # diagonal
            (1, -1),   # diagonal
        ]
    else:
        neighbor_offsets = [
            (0, 1),    # rechts
            (1, 0),    # oben/unten
        ]

    node_by_id = {
        row["node_id"]: row
        for row in nodes
    }

    edges = []
    edge_id = 0

    for node in nodes:
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

            # Verbindung darf nicht durch oder über eine Zone laufen
            if line.intersects(forbidden_area):
                continue

            length_m = line.length

            edges.append({
                "edge_id": edge_id,
                "from_node": from_id,
                "to_node": to_id,
                "length_m": length_m,
                "spacing_m": spacing_m,
                "diagonal": length_m > spacing_m * 1.01,
                "geometry": line,
            })

            edge_id += 1

    edges_gdf = gpd.GeoDataFrame(
        edges,
        geometry="geometry",
        crs=metric_crs,
    )

    if edges_gdf.empty:
        raise ValueError("Es wurden keine erlaubten Kanten erzeugt.")

    # -------------------------------------------------
    # 3. Als GeoJSON speichern
    # -------------------------------------------------

    nodes_path.parent.mkdir(parents=True, exist_ok=True)
    edges_path.parent.mkdir(parents=True, exist_ok=True)

    nodes_gdf.to_crs(WGS84).to_file(nodes_path, driver="GeoJSON")
    edges_gdf.to_crs(WGS84).to_file(edges_path, driver="GeoJSON")

    return nodes_gdf.to_crs(WGS84), edges_gdf.to_crs(WGS84)


def main():
    zones_geojson = "drohnen_luftvo_zonen.geojson"
    output_nodes = "graph_nodes.geojson"
    output_edges = "graph_edges.geojson"

    nodes, edges = create_navigation_graph(
        zones_geojson=zones_geojson,
        output_nodes_geojson=output_nodes,
        output_edges_geojson=output_edges,
        spacing_m=250,
        connect_diagonal=True,
        bbox_padding_m=0,
    )

    print("Graph wurde erzeugt.")
    print(f"Knoten: {len(nodes)}")
    print(f"Kanten: {len(edges)}")
    print(f"Nodes-Datei: {output_nodes}")
    print(f"Edges-Datei: {output_edges}")


if __name__ == "__main__":
    main()