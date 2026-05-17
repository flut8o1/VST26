from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, LineString

from utils import WGS84, DEFAULT_METRIC_CRS, get_geometry_union, wgs84_to_metric, prepare_geometry


def _edge_orientation(from_point, to_point, tolerance=0.001):
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
    forbidden,
    edges,
    start_edge_id,
    edge_kind,
    max_connections,
):
    if max_connections < 1:
        raise ValueError("special_connections_per_point muss mindestens 1 sein.")

    special_point = special_node["geometry"]

    candidates = sorted(
        [
            (special_point.distance(n["geometry"]), n)
            for n in grid_nodes
            if special_point.distance(n["geometry"]) > 0
        ],
        key=lambda item: item[0],
    )

    edge_id             = start_edge_id
    created_connections = 0

    for _, grid_node in candidates:
        grid_point = grid_node["geometry"]
        line       = LineString([special_point, grid_point])

        if forbidden.intersects(line):
            continue

        orientation = _edge_orientation(special_point, grid_point)

        edges.append({
            "edge_id":          edge_id,
            "from_node":        special_node["node_id"],
            "to_node":          grid_node["node_id"],
            "length_m":         line.length,
            "spacing_m":        None,
            "diagonal":         orientation == "diagonal",
            "connect_diagonal": None,
            "edge_kind":        edge_kind,
            "orientation":      orientation,
            "geometry":         line,
        })

        edge_id             += 1
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
    max_bbox_wgs84=None,
    start_lat=None,
    start_lon=None,
    end_lat=None,
    end_lon=None,
    special_connections_per_point=1,
):
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

    zones        = zones.set_crs(WGS84) if zones.crs is None else zones.to_crs(WGS84)
    zones_metric = zones.to_crs(metric_crs)
    zones_metric["geometry"] = zones_metric.geometry.buffer(0)

    forbidden_area = get_geometry_union(zones_metric)
    forbidden      = prepare_geometry(forbidden_area)

    special_points = []

    if start_lat is not None and start_lon is not None:
        pt = wgs84_to_metric(start_lat, start_lon, metric_crs)
        special_points.append({"node_kind": "start", "label": "Start", "geometry": pt})

    if end_lat is not None and end_lon is not None:
        pt = wgs84_to_metric(end_lat, end_lon, metric_crs)
        special_points.append({"node_kind": "end", "label": "Ende", "geometry": pt})

    for sp in special_points:
        if forbidden.intersects(sp["geometry"]):
            raise ValueError(f"{sp['label']} liegt innerhalb oder auf einer Sperrzone.")

    minx, miny, maxx, maxy = zones_metric.total_bounds

    for sp in special_points:
        pt   = sp["geometry"]
        minx = min(minx, pt.x)
        miny = min(miny, pt.y)
        maxx = max(maxx, pt.x)
        maxy = max(maxy, pt.y)

    minx -= bbox_padding_m
    miny -= bbox_padding_m
    maxx += bbox_padding_m
    maxy += bbox_padding_m

    if max_bbox_wgs84 is not None:
        _sw  = wgs84_to_metric(max_bbox_wgs84[0], max_bbox_wgs84[1], metric_crs)
        _ne  = wgs84_to_metric(max_bbox_wgs84[2], max_bbox_wgs84[3], metric_crs)
        minx = max(minx, _sw.x)
        miny = max(miny, _sw.y)
        maxx = min(maxx, _ne.x)
        maxy = min(maxy, _ne.y)

    grid_nodes  = []
    node_lookup = {}
    row_index   = 0
    y           = miny

    while y <= maxy:
        col_index = 0
        x         = minx

        while x <= maxx:
            point = Point(x, y)

            if not forbidden.intersects(point):
                node_id = len(grid_nodes)

                grid_nodes.append({
                    "node_id":   node_id,
                    "node_kind": "grid",
                    "label":     None,
                    "grid_row":  row_index,
                    "grid_col":  col_index,
                    "x":         x,
                    "y":         y,
                    "spacing_m": spacing_m,
                    "geometry":  point,
                })

                node_lookup[(row_index, col_index)] = node_id

            x += spacing_m
            col_index += 1

        y += spacing_m
        row_index += 1

    if not grid_nodes:
        raise ValueError("Es wurden keine erlaubten Gitterknoten erzeugt.")

    nodes = list(grid_nodes)

    for sp in special_points:
        pt = sp["geometry"]
        nodes.append({
            "node_id":   len(nodes),
            "node_kind": sp["node_kind"],
            "label":     sp["label"],
            "grid_row":  None,
            "grid_col":  None,
            "x":         pt.x,
            "y":         pt.y,
            "spacing_m": None,
            "geometry":  pt,
        })

    neighbor_offsets = [(0, 1), (1, 0)]
    if connect_diagonal:
        neighbor_offsets += [(1, 1), (1, -1)]

    node_by_id = {n["node_id"]: n for n in nodes}
    edges      = []
    edge_id    = 0

    for node in grid_nodes:
        from_id    = node["node_id"]
        from_point = node["geometry"]
        row, col   = node["grid_row"], node["grid_col"]

        for d_row, d_col in neighbor_offsets:
            neighbor_key = (row + d_row, col + d_col)

            if neighbor_key not in node_lookup:
                continue

            to_id    = node_lookup[neighbor_key]
            to_point = node_by_id[to_id]["geometry"]
            line     = LineString([from_point, to_point])

            if forbidden.intersects(line):
                continue

            orientation = _edge_orientation(from_point, to_point)

            edges.append({
                "edge_id":          edge_id,
                "from_node":        from_id,
                "to_node":          to_id,
                "length_m":         line.length,
                "spacing_m":        spacing_m,
                "diagonal":         orientation == "diagonal",
                "connect_diagonal": connect_diagonal,
                "edge_kind":        "grid",
                "orientation":      orientation,
                "geometry":         line,
            })

            edge_id += 1

    for node in [n for n in nodes if n["node_kind"] in {"start", "end"}]:
        edge_id = _connect_special_node_to_grid(
            special_node=node,
            grid_nodes=grid_nodes,
            forbidden=forbidden,
            edges=edges,
            start_edge_id=edge_id,
            edge_kind=f"{node['node_kind']}_connection",
            max_connections=special_connections_per_point,
        )

    if not edges:
        raise ValueError("Es wurden keine erlaubten Kanten erzeugt.")

    nodes_gdf = gpd.GeoDataFrame(nodes, geometry="geometry", crs=metric_crs)
    edges_gdf = gpd.GeoDataFrame(edges, geometry="geometry", crs=metric_crs)

    nodes_path.parent.mkdir(parents=True, exist_ok=True)
    edges_path.parent.mkdir(parents=True, exist_ok=True)

    nodes_wgs84 = nodes_gdf.to_crs(WGS84)
    edges_wgs84 = edges_gdf.to_crs(WGS84)

    nodes_wgs84.to_file(nodes_path, driver="GeoJSON")
    edges_wgs84.to_file(edges_path, driver="GeoJSON")

    return nodes_wgs84, edges_wgs84
