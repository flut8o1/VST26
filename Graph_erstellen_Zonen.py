"""
Graph_erstellen_Zonen.py – Erzeugung eines Sichtbarkeitsgraphen um LuftVO-Zonen.

Statt eines regelmäßigen Gitters werden Knoten 10 m außerhalb jeder Ecke
der Sperrzonen platziert. Kanten werden zwischen allen Knotenpaaren gezogen,
die sich gegenseitig „sehen" (d. h. deren Verbindungslinie keine Sperrzone
schneidet). Optional werden sich kreuzende Kanten herausgefiltert.

Start- und Endpunkt werden als zusätzliche Knoten eingefügt.
"""

from pathlib import Path
from math import sqrt, cos, sin, pi

import geopandas as gpd
from shapely.geometry import Point, LineString
from shapely.geometry.polygon import orient

from utils import WGS84, DEFAULT_METRIC_CRS, get_geometry_union, wgs84_to_metric


# =============================================================================
# Geometrie-Hilfsfunktionen für Außenrichtungen an Zonenecken
# =============================================================================

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
    """Normiert einen Vektor auf Länge 1. Gibt None zurück bei Nullvektor."""
    length = sqrt(dx * dx + dy * dy)

    if length == 0:
        return None

    return dx / length, dy / length


def _right_normal(dx, dy):
    """
    Berechnet die rechte Normalenrichtung eines Vektors.

    Für CCW-orientierte Außenringe zeigt die rechte Normale nach außen.
    """
    unit = _unit_vector(dx, dy)

    if unit is None:
        return None

    ux, uy = unit
    return uy, -ux


def _vertex_outward_direction(previous_coord, current_coord, next_coord):
    """
    Berechnet die Außenrichtung an einer Polygonecke.

    Die Polygone werden vorher auf CCW-Orientierung (counter-clockwise) gebracht.
    Für CCW-Außenringe zeigt die rechte Normale nach außen, daher wird der
    Mittelwert der rechten Normalen der beiden angrenzenden Kanten genutzt.
    """
    px, py = previous_coord
    cx, cy = current_coord
    nx, ny = next_coord

    # Richtungsvektoren der angrenzenden Kanten
    n1 = _right_normal(cx - px, cy - py)
    n2 = _right_normal(nx - cx, ny - cy)

    if n1 is None and n2 is None:
        return None
    if n1 is None:
        return n2
    if n2 is None:
        return n1

    # Mittelwert der beiden Außennormalen normiert
    direction = _unit_vector(n1[0] + n2[0], n1[1] + n2[1])

    # Wenn der Mittelwert ein Nullvektor ist (Kehrtwende), Fallback auf n2.
    return direction if direction is not None else n2


def _candidate_offset_point(vertex_coord, direction, offset_m, forbidden_area):
    """
    Erstellt einen Knoten im Abstand offset_m außerhalb einer Zonenecke.

    Zuerst wird die berechnete Außenrichtung probiert. Falls der Kandidat
    trotzdem in einer Sperrzone liegt (z. B. bei sehr engen Winkeln),
    werden 32 gleichmäßig verteilte Richtungen im Kreis getestet.

    Rückgabe:
        Shapely Point oder None, wenn alle Richtungen blockiert sind.
    """
    x, y = vertex_coord

    if direction is not None:
        dx, dy    = direction
        candidate = Point(x + dx * offset_m, y + dy * offset_m)

        if not candidate.intersects(forbidden_area):
            return candidate

    # Fallback: Kreis-Scan mit 32 gleichmäßig verteilten Richtungen.
    for i in range(32):
        angle     = 2 * pi * i / 32
        candidate = Point(x + cos(angle) * offset_m, y + sin(angle) * offset_m)

        if not candidate.intersects(forbidden_area):
            return candidate

    return None


# =============================================================================
# Kantenschnitt-Prüfung
# =============================================================================

def _line_crosses_existing_edges(line, from_node, to_node, existing_edges):
    """
    Prüft, ob eine neue Kante eine bereits gewählte Kante schneidet.

    Kanten dürfen sich an einem gemeinsamen Endpunkt treffen –
    nur echte Kreuzungen (ohne gemeinsamen Knoten) werden abgelehnt.
    """
    new_nodes = {from_node, to_node}

    for edge in existing_edges:
        existing_nodes = {edge["from_node"], edge["to_node"]}

        # Gemeinsame Endpunkte sind kein Schnitt.
        if new_nodes & existing_nodes:
            continue

        if line.intersects(edge["geometry"]):
            return True

    return False


# =============================================================================
# Knoten-Erzeugung
# =============================================================================

def _create_zone_corner_nodes(zones_metric, forbidden_area, *, offset_m=10):
    """
    Erstellt Knoten im Abstand offset_m außerhalb jeder Ecke der Sperrzonen.

    Für jede Ecke jedes Polygons wird eine Außenrichtung berechnet und
    ein Kandidatenpunkt im Abstand offset_m platziert. Doppelte Knoten
    (auf 2 Dezimalstellen gerundet) werden übersprungen.
    """
    nodes      = []
    used_points = set()

    for _, row in zones_metric.iterrows():
        geom = row.geometry

        for polygon in _iter_polygons(geom):
            if polygon.is_empty:
                continue

            # CCW-Orientierung sicherstellen, damit die rechte Normale nach außen zeigt.
            polygon      = orient(polygon, sign=1.0)
            coords       = list(polygon.exterior.coords)

            # Letzter Punkt ist identisch mit erstem – für den Ring entfernen.
            if len(coords) < 4:
                continue

            ring        = coords[:-1]
            ring_length = len(ring)

            for i, current_coord in enumerate(ring):
                previous_coord = ring[(i - 1) % ring_length]
                next_coord     = ring[(i + 1) % ring_length]

                direction = _vertex_outward_direction(previous_coord, current_coord, next_coord)

                candidate = _candidate_offset_point(
                    vertex_coord=current_coord,
                    direction=direction,
                    offset_m=offset_m,
                    forbidden_area=forbidden_area,
                )

                if candidate is None:
                    continue

                # Doppelte Knoten vermeiden (auf 2 Dezimalstellen gerundet).
                key = (round(candidate.x, 2), round(candidate.y, 2))

                if key in used_points:
                    continue

                used_points.add(key)

                nodes.append({
                    "node_id":      len(nodes),
                    "node_kind":    "zone",
                    "label":        "Zone",
                    "grid_row":     None,
                    "grid_col":     None,
                    "x":            candidate.x,
                    "y":            candidate.y,
                    "spacing_m":    None,
                    "zone_offset_m": offset_m,
                    "geometry":     candidate,
                })

    return nodes


# =============================================================================
# Haupt-Pipeline-Funktion
# =============================================================================

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
    Erstellt einen Sichtbarkeitsgraphen um LuftVO-Sperrzonen.

    Prinzip:
    - Von jeder Ecke der Sperrzonen wird im Abstand node_offset_m ein Knoten erzeugt.
    - Start- und Endpunkt werden als zusätzliche Knoten eingefügt.
    - Alle Knotenpaare, die sich gegenseitig sehen können (keine Sperrzone
      auf der Verbindungslinie), werden als Kanten aufgenommen.
    - Optional: Kanten, die andere Kanten kreuzen, werden verworfen.
      Bei Konflikten werden die kürzesten Kanten bevorzugt.

    Rückgabe:
        (nodes_wgs84, edges_wgs84) – beide als GeoDataFrames in WGS84.
    """
    zones_path = Path(zones_geojson)
    nodes_path = Path(output_nodes_geojson)
    edges_path = Path(output_edges_geojson)

    if not zones_path.exists():
        raise FileNotFoundError(f"Zonen-Datei nicht gefunden: {zones_path}")

    if node_offset_m <= 0:
        raise ValueError("node_offset_m muss größer als 0 sein.")

    # --- Sperrzonen laden und vereinigen ---

    zones = gpd.read_file(zones_path)

    if zones.empty:
        raise ValueError("Die Zonen-Datei enthält keine Features.")

    zones        = zones.set_crs(WGS84) if zones.crs is None else zones.to_crs(WGS84)
    zones_metric = zones.to_crs(metric_crs)

    # buffer(0) behebt mögliche Topologie-Fehler in den Zonengeometrien.
    zones_metric["geometry"] = zones_metric.geometry.buffer(0)

    forbidden_area = get_geometry_union(zones_metric)

    # ==========================================================================
    # Schritt 1: Knoten um Zonenecken erzeugen
    # ==========================================================================

    nodes = _create_zone_corner_nodes(
        zones_metric=zones_metric,
        forbidden_area=forbidden_area,
        offset_m=node_offset_m,
    )

    if not nodes:
        raise ValueError("Es konnten keine Knoten aus Zonenecken erzeugt werden.")

    # ==========================================================================
    # Schritt 2: Start- und Endknoten hinzufügen
    # ==========================================================================

    if start_lat is not None and start_lon is not None:
        start_point = wgs84_to_metric(start_lat, start_lon, metric_crs)

        if start_point.intersects(forbidden_area):
            raise ValueError("Startpunkt liegt innerhalb oder auf einer Sperrzone.")

        nodes.append({
            "node_id":       len(nodes),
            "node_kind":     "start",
            "label":         "Start",
            "grid_row":      None,
            "grid_col":      None,
            "x":             start_point.x,
            "y":             start_point.y,
            "spacing_m":     None,
            "zone_offset_m": None,
            "geometry":      start_point,
        })

    if end_lat is not None and end_lon is not None:
        end_point = wgs84_to_metric(end_lat, end_lon, metric_crs)

        if end_point.intersects(forbidden_area):
            raise ValueError("Endpunkt liegt innerhalb oder auf einer Sperrzone.")

        nodes.append({
            "node_id":       len(nodes),
            "node_kind":     "end",
            "label":         "Ende",
            "grid_row":      None,
            "grid_col":      None,
            "x":             end_point.x,
            "y":             end_point.y,
            "spacing_m":     None,
            "zone_offset_m": None,
            "geometry":      end_point,
        })

    # ==========================================================================
    # Schritt 3: Sichtbare Kantenkandidaten erzeugen
    # ==========================================================================

    candidates = []

    for i in range(len(nodes)):
        from_node = nodes[i]

        for j in range(i + 1, len(nodes)):
            to_node = nodes[j]

            from_point = from_node["geometry"]
            to_point   = to_node["geometry"]
            line       = LineString([from_point, to_point])
            length_m   = line.length

            # Kanten jenseits der maximalen Länge sofort verwerfen.
            if max_edge_distance_m is not None and length_m > max_edge_distance_m:
                continue

            # Kante verwerfen, wenn sie eine Sperrzone schneidet.
            if line.intersects(forbidden_area):
                continue

            candidates.append({
                "from_node": from_node["node_id"],
                "to_node":   to_node["node_id"],
                "length_m":  length_m,
                "geometry":  line,
            })

    if not candidates:
        raise ValueError("Es wurden keine sichtbaren Kantenkandidaten gefunden.")

    # Kürzeste Kanten zuerst: Bei Kreuzungsfilterung bleiben die kürzeren erhalten.
    candidates.sort(key=lambda e: e["length_m"])

    # ==========================================================================
    # Schritt 4: Kanten auswählen (optional ohne Kreuzungen)
    # ==========================================================================

    edges = []

    for candidate in candidates:
        line      = candidate["geometry"]
        from_node = candidate["from_node"]
        to_node   = candidate["to_node"]

        # Wenn aktiviert: Kante verwerfen, falls sie eine bereits gewählte kreuzt.
        if prevent_edge_crossings:
            if _line_crosses_existing_edges(line, from_node, to_node, edges):
                continue

        edges.append({
            "edge_id":          len(edges),
            "from_node":        from_node,
            "to_node":          to_node,
            "length_m":         candidate["length_m"],
            "spacing_m":        None,
            "diagonal":         None,
            "connect_diagonal": None,
            "edge_kind":        "visibility",
            "orientation":      "visibility",
            "geometry":         line,
        })

    if not edges:
        raise ValueError("Es wurden keine erlaubten Kanten erzeugt.")

    # ==========================================================================
    # Schritt 5: Als GeoJSON speichern
    # ==========================================================================

    nodes_gdf = gpd.GeoDataFrame(nodes, geometry="geometry", crs=metric_crs)
    edges_gdf = gpd.GeoDataFrame(edges, geometry="geometry", crs=metric_crs)

    nodes_path.parent.mkdir(parents=True, exist_ok=True)
    edges_path.parent.mkdir(parents=True, exist_ok=True)

    nodes_wgs84 = nodes_gdf.to_crs(WGS84)
    edges_wgs84 = edges_gdf.to_crs(WGS84)

    nodes_wgs84.to_file(nodes_path, driver="GeoJSON")
    edges_wgs84.to_file(edges_path, driver="GeoJSON")

    return nodes_wgs84, edges_wgs84
