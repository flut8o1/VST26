from pathlib import Path
from math import sqrt, cos, sin, pi

import geopandas as gpd
from shapely.geometry import Point, LineString
from shapely.geometry.polygon import orient


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


def _iter_polygons(geometry):
    """
    Gibt alle Polygone aus Polygon, MultiPolygon oder GeometryCollection zurück.
    """

    if geometry is None or geometry.is_empty:
        return

    if geometry.geom_type == "Polygon":
        yield geometry

    elif geometry.geom_type == "MultiPolygon":
        for polygon in geometry.geoms:
            yield polygon

    elif geometry.geom_type == "GeometryCollection":
        for part in geometry.geoms:
            yield from _iter_polygons(part)


def _unit_vector(dx, dy):
    length = sqrt(dx * dx + dy * dy)

    if length == 0:
        return None

    return dx / length, dy / length


def _right_normal(dx, dy):
    """
    Rechte Normalenrichtung eines Vektors.
    Für außenorientierte Polygonringe wird das für die Außenrichtung genutzt.
    """

    unit = _unit_vector(dx, dy)

    if unit is None:
        return None

    ux, uy = unit
    return uy, -ux


def _vertex_outward_direction(previous_coord, current_coord, next_coord):
    """
    Berechnet eine Außenrichtung für eine Polygonecke.
    Die Polygone werden vorher auf CCW-Orientierung gebracht.
    Für CCW-Außenringe zeigt die rechte Normale nach außen.
    """

    px, py = previous_coord
    cx, cy = current_coord
    nx, ny = next_coord

    dx1 = cx - px
    dy1 = cy - py

    dx2 = nx - cx
    dy2 = ny - cy

    n1 = _right_normal(dx1, dy1)
    n2 = _right_normal(dx2, dy2)

    if n1 is None and n2 is None:
        return None

    if n1 is None:
        return n2

    if n2 is None:
        return n1

    sx = n1[0] + n2[0]
    sy = n1[1] + n2[1]

    direction = _unit_vector(sx, sy)

    if direction is None:
        return n2

    return direction


def _candidate_offset_point(vertex_coord, direction, offset_m, forbidden_area):
    """
    Erstellt einen Punkt im Abstand offset_m von einer Ecke.
    Falls die berechnete Außenrichtung in einer Zone landet, werden mehrere
    Richtungen im Kreis getestet.
    """

    x, y = vertex_coord

    if direction is not None:
        dx, dy = direction
        candidate = Point(x + dx * offset_m, y + dy * offset_m)

        if not candidate.intersects(forbidden_area):
            return candidate

    # Fallback: exakt offset_m Abstand in mehreren Richtungen testen
    for i in range(32):
        angle = 2 * pi * i / 32
        candidate = Point(
            x + cos(angle) * offset_m,
            y + sin(angle) * offset_m,
        )

        if not candidate.intersects(forbidden_area):
            return candidate

    return None


def _wgs84_point_to_metric(lat, lon, metric_crs):
    point_wgs84 = gpd.GeoSeries(
        [Point(lon, lat)],
        crs=WGS84,
    )

    return point_wgs84.to_crs(metric_crs).iloc[0]


def _line_crosses_existing_edges(line, from_node, to_node, existing_edges):
    """
    Prüft, ob eine neue Kante eine bereits gewählte Kante schneidet.
    Kanten dürfen sich am gemeinsamen Knoten treffen.
    """

    new_nodes = {from_node, to_node}

    for edge in existing_edges:
        existing_nodes = {edge["from_node"], edge["to_node"]}

        # Gemeinsame Endpunkte sind erlaubt
        if new_nodes & existing_nodes:
            continue

        if line.intersects(edge["geometry"]):
            return True

    return False


def _create_zone_corner_nodes(
    zones_metric,
    forbidden_area,
    *,
    offset_m=10,
):
    """
    Erstellt Knoten im Abstand offset_m von jeder Ecke der Zonen.
    """

    nodes = []
    used_points = set()

    for _, row in zones_metric.iterrows():
        geom = row.geometry

        for polygon in _iter_polygons(geom):
            if polygon.is_empty:
                continue

            polygon = orient(polygon, sign=1.0)
            coords = list(polygon.exterior.coords)

            # Letzter Punkt ist identisch mit erstem Punkt
            if len(coords) < 4:
                continue

            ring = coords[:-1]
            ring_length = len(ring)

            for i, current_coord in enumerate(ring):
                previous_coord = ring[(i - 1) % ring_length]
                next_coord = ring[(i + 1) % ring_length]

                direction = _vertex_outward_direction(
                    previous_coord=previous_coord,
                    current_coord=current_coord,
                    next_coord=next_coord,
                )

                candidate = _candidate_offset_point(
                    vertex_coord=current_coord,
                    direction=direction,
                    offset_m=offset_m,
                    forbidden_area=forbidden_area,
                )

                if candidate is None:
                    continue

                # Doppelte Knoten vermeiden
                key = (round(candidate.x, 2), round(candidate.y, 2))

                if key in used_points:
                    continue

                used_points.add(key)

                nodes.append({
                    "node_id": len(nodes),
                    "node_kind": "zone",
                    "label": "Zone",
                    "grid_row": None,
                    "grid_col": None,
                    "x": candidate.x,
                    "y": candidate.y,
                    "spacing_m": None,
                    "zone_offset_m": offset_m,
                    "geometry": candidate,
                })

    return nodes


def create_zone_visibility_graph(
    zones_geojson,
    output_nodes_geojson,
    output_edges_geojson,
    *,
    metric_crs=DEFAULT_METRIC_CRS,
    node_offset_m=10,
    prevent_edge_crossings=True,
    max_edge_distance_m=None,

    start_lat=None,
    start_lon=None,
    end_lat=None,
    end_lon=None,
):
    """
    Erstellt einen Sichtbarkeitsgraphen um Zonen.

    Prinzip:
        - Kein regelmäßiges Grid.
        - Von jeder Ecke der Zonen wird im Abstand node_offset_m ein Knoten erzeugt.
        - Start- und Endpunkt werden als zusätzliche Knoten eingefügt.
        - Alle sichtbaren Knotenpaare werden als mögliche Kanten geprüft.
        - Eine Kante ist nur erlaubt, wenn sie keine Zone schneidet.
        - Optional dürfen Kanten auch keine anderen Kanten schneiden.
        - Bei Konflikten werden die kürzesten Kanten zuerst übernommen.

    Ausgabe:
        - Nodes als GeoJSON
        - Edges als GeoJSON
    """

    zones_path = Path(zones_geojson)
    nodes_path = Path(output_nodes_geojson)
    edges_path = Path(output_edges_geojson)

    if not zones_path.exists():
        raise FileNotFoundError(f"Zonen-Datei nicht gefunden: {zones_path}")

    if node_offset_m <= 0:
        raise ValueError("node_offset_m muss größer als 0 sein.")

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

    forbidden_area = _get_geometry_union(zones_metric)

    # -------------------------------------------------
    # 1. Knoten um Zonenecken erzeugen
    # -------------------------------------------------

    nodes = _create_zone_corner_nodes(
        zones_metric=zones_metric,
        forbidden_area=forbidden_area,
        offset_m=node_offset_m,
    )

    if not nodes:
        raise ValueError("Es konnten keine Knoten aus Zonenecken erzeugt werden.")

    # -------------------------------------------------
    # 2. Start- und Endknoten hinzufügen
    # -------------------------------------------------

    if start_lat is not None and start_lon is not None:
        start_point = _wgs84_point_to_metric(
            lat=start_lat,
            lon=start_lon,
            metric_crs=metric_crs,
        )

        if start_point.intersects(forbidden_area):
            raise ValueError("Startpunkt liegt innerhalb oder auf einer Sperrzone.")

        nodes.append({
            "node_id": len(nodes),
            "node_kind": "start",
            "label": "Start",
            "grid_row": None,
            "grid_col": None,
            "x": start_point.x,
            "y": start_point.y,
            "spacing_m": None,
            "zone_offset_m": None,
            "geometry": start_point,
        })

    if end_lat is not None and end_lon is not None:
        end_point = _wgs84_point_to_metric(
            lat=end_lat,
            lon=end_lon,
            metric_crs=metric_crs,
        )

        if end_point.intersects(forbidden_area):
            raise ValueError("Endpunkt liegt innerhalb oder auf einer Sperrzone.")

        nodes.append({
            "node_id": len(nodes),
            "node_kind": "end",
            "label": "Ende",
            "grid_row": None,
            "grid_col": None,
            "x": end_point.x,
            "y": end_point.y,
            "spacing_m": None,
            "zone_offset_m": None,
            "geometry": end_point,
        })

    # -------------------------------------------------
    # 3. Sichtbare Kantenkandidaten erzeugen
    # -------------------------------------------------

    candidates = []

    for i in range(len(nodes)):
        from_node = nodes[i]

        for j in range(i + 1, len(nodes)):
            to_node = nodes[j]

            from_point = from_node["geometry"]
            to_point = to_node["geometry"]

            line = LineString([from_point, to_point])

            length_m = line.length

            if max_edge_distance_m is not None and length_m > max_edge_distance_m:
                continue

            # Kante darf keine Sperrzone schneiden
            if line.intersects(forbidden_area):
                continue

            candidates.append({
                "from_node": from_node["node_id"],
                "to_node": to_node["node_id"],
                "length_m": length_m,
                "geometry": line,
            })

    if not candidates:
        raise ValueError("Es wurden keine sichtbaren Kantenkandidaten gefunden.")

    # Kürzeste Kanten zuerst
    candidates.sort(key=lambda edge: edge["length_m"])

    # -------------------------------------------------
    # 4. Kanten auswählen
    # -------------------------------------------------

    edges = []

    for candidate in candidates:
        line = candidate["geometry"]
        from_node = candidate["from_node"]
        to_node = candidate["to_node"]

        if prevent_edge_crossings:
            if _line_crosses_existing_edges(
                line=line,
                from_node=from_node,
                to_node=to_node,
                existing_edges=edges,
            ):
                continue

        edges.append({
            "edge_id": len(edges),
            "from_node": from_node,
            "to_node": to_node,
            "length_m": candidate["length_m"],
            "spacing_m": None,
            "diagonal": None,
            "connect_diagonal": None,
            "edge_kind": "visibility",
            "orientation": "visibility",
            "geometry": line,
        })

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
    # 5. Speichern
    # -------------------------------------------------

    nodes_path.parent.mkdir(parents=True, exist_ok=True)
    edges_path.parent.mkdir(parents=True, exist_ok=True)

    nodes_wgs84 = nodes_gdf.to_crs(WGS84)
    edges_wgs84 = edges_gdf.to_crs(WGS84)

    nodes_wgs84.to_file(nodes_path, driver="GeoJSON")
    edges_wgs84.to_file(edges_path, driver="GeoJSON")

    return nodes_wgs84, edges_wgs84


def main():
    nodes, edges = create_zone_visibility_graph(
        zones_geojson="drohnen_luftvo_zonen.geojson",
        output_nodes_geojson="graph_nodes.geojson",
        output_edges_geojson="graph_edges.geojson",

        node_offset_m=10,
        prevent_edge_crossings=True,
        max_edge_distance_m=None,

        start_lat=48.14018493850112,
        start_lon=11.56075451365665,
        end_lat=48.07276903757395,
        end_lon=11.637402988925738,
    )

    print("Zonen-Sichtbarkeitsgraph wurde erzeugt.")
    print(f"Knoten: {len(nodes)}")
    print(f"Kanten: {len(edges)}")


if __name__ == "__main__":
    main()