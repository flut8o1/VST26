"""
Graph_erstellen_Zonen.py – Sichtbarkeitsgraph mit Knoten an Zonenecken.

Algorithmus in vier Schritten:

  1. Knoten + Ringzugehörigkeit
     Für jede Polygonecke jeder Sperrzone wird ein Knoten offset_m Meter
     außerhalb der Ecke platziert. Die Zugehörigkeit zu jedem Polygon-Ring
     wird mitgespeichert.

  2. Ring-Kanten (Zonenrand-Traversierung)
     Benachbarte Ecken desselben Rings werden als erste Kanten verbunden
     (sofern die Verbindung keine Zone schneidet). Das stellt sicher, dass
     eine Drohne immer um eine Zone herumfliegen kann.

  3. Sichtbarkeitskanten (kreuzungsfrei)
     Alle übrigen Knotenpaare (inkl. Start/End) werden als Kandidaten
     geprüft: kürzeste zuerst, verworfen wenn sie eine Zone oder eine
     bereits akzeptierte Kante kreuzen. Statt intersects() wird crosses()
     verwendet – damit werden gemeinsame Endpunkte (erlaubt) automatisch
     korrekt behandelt, ohne manuelle Mengenoperation.

  4. Speichern als GeoJSON

Performance:
    - Zonenschnitt-Tests: prep() (PreparedGeometry) für Knoten/Ring-Kanten;
      vektorisiertes shapely.intersects() (GEOS-Batch) für den O(N²)-Kandidaten-Scan.
    - Kreuzungstest: STRtree mit predicate='crosses' reduziert den pro-Kante-Check
      von O(E) auf O(log E + k). Eine Pending-Liste von max. REBUILD_EVERY Kanten
      wird linear geprüft und danach in den Baum überführt.
"""

from pathlib import Path
from math import sqrt, cos, sin, pi

import numpy as np
import shapely
import geopandas as gpd
from shapely import STRtree
from shapely.geometry import Point, LineString
from shapely.geometry.polygon import orient

from utils import (
    WGS84, DEFAULT_METRIC_CRS,
    get_geometry_union, wgs84_to_metric, prepare_geometry,
)


# =============================================================================
# Geometrie-Hilfsfunktionen: Polygone iterieren
# =============================================================================

def _iter_polygons(geometry):
    """Gibt alle Polygone aus Polygon, MultiPolygon oder GeometryCollection zurück."""
    if geometry is None or geometry.is_empty:
        return

    if geometry.geom_type == "Polygon":
        yield geometry
    elif geometry.geom_type == "MultiPolygon":
        for poly in geometry.geoms:
            yield poly
    elif geometry.geom_type == "GeometryCollection":
        for part in geometry.geoms:
            yield from _iter_polygons(part)


# =============================================================================
# Geometrie-Hilfsfunktionen: Außenrichtung an Polygonecken
# =============================================================================

def _unit_vector(dx, dy):
    """Normiert einen Vektor auf Länge 1. Gibt None zurück bei Nullvektor."""
    length = sqrt(dx * dx + dy * dy)
    return (dx / length, dy / length) if length > 0 else None


def _right_normal(dx, dy):
    """
    Rechte Normalenrichtung eines Vektors (um 90° im Uhrzeigersinn gedreht).

    Für CCW-orientierte Außenringe zeigt die rechte Normale nach außen –
    das ist die Richtung, in die wir den Offset-Knoten platzieren wollen.
    """
    unit = _unit_vector(dx, dy)
    if unit is None:
        return None
    ux, uy = unit
    return uy, -ux


def _vertex_outward_direction(prev, curr, nxt):
    """
    Berechnet die Außenrichtung an einer Polygonecke als Mittelnormale
    der beiden angrenzenden Kanten.

    prev / curr / nxt:
        Koordinatentuples der Vorgänger-, aktuellen und Nachfolgerecke.
    """
    n1 = _right_normal(curr[0] - prev[0], curr[1] - prev[1])
    n2 = _right_normal(nxt[0]  - curr[0], nxt[1]  - curr[1])

    if n1 is None and n2 is None:
        return None
    if n1 is None:
        return n2
    if n2 is None:
        return n1

    # Mittelnormale normieren; Fallback auf n2 bei Nullvektor (Kehrtwende).
    direction = _unit_vector(n1[0] + n2[0], n1[1] + n2[1])
    return direction if direction is not None else n2


def _candidate_offset_point(vertex_coord, direction, offset_m, forbidden):
    """
    Platziert einen Knoten offset_m Meter außerhalb einer Polygonecke.

    Zuerst wird die berechnete Außenrichtung versucht. Falls der Punkt
    trotzdem in einer Sperrzone liegt (z. B. bei sehr engen Winkeln),
    werden 32 gleichmäßig verteilte Richtungen im Kreis getestet.

    forbidden:
        PreparedGeometry der vereinigten Sperrzonen.

    Rückgabe:
        Shapely Point oder None, wenn alle Richtungen blockiert sind.
    """
    x, y = vertex_coord

    if direction is not None:
        dx, dy    = direction
        candidate = Point(x + dx * offset_m, y + dy * offset_m)
        if not forbidden.intersects(candidate):
            return candidate

    # Kreis-Scan als Fallback.
    for i in range(32):
        angle     = 2 * pi * i / 32
        candidate = Point(x + cos(angle) * offset_m, y + sin(angle) * offset_m)
        if not forbidden.intersects(candidate):
            return candidate

    return None


# =============================================================================
# Schritt 1: Knoten erzeugen und Ringzugehörigkeit tracken
# =============================================================================

def _create_nodes_and_rings(zones_metric, forbidden, *, offset_m):
    """
    Erzeugt Knoten an den Außenecken jedes Sperrzonenpolygons.

    Gibt neben den Knoten auch die Ringzugehörigkeit zurück: eine Liste
    von Listen, wobei jede innere Liste die node_ids eines Polygon-Rings
    in Reihenfolge enthält (None für Ecken, bei denen kein freier Punkt
    gefunden wurde).

    Doppelte Positionen (gleiche Koordinaten auf 2 Dezimalstellen) werden
    dedupliziert – der erste erzeugte Knoten an dieser Position wird
    wiederverwendet.

    Rückgabe:
        (nodes, rings)
    """
    nodes = []
    rings = []
    seen  = {}   # (rounded_x, rounded_y) -> node_id

    for _, row in zones_metric.iterrows():
        for polygon in _iter_polygons(row.geometry):
            if polygon.is_empty:
                continue

            # CCW-Orientierung sicherstellen: rechte Normale zeigt nach außen.
            polygon = orient(polygon, sign=1.0)
            coords  = list(polygon.exterior.coords)[:-1]   # schließenden Punkt entfernen

            if len(coords) < 3:
                continue

            n        = len(coords)
            ring_ids = []

            for i, cc in enumerate(coords):
                pc        = coords[(i - 1) % n]
                nc        = coords[(i + 1) % n]
                direction = _vertex_outward_direction(pc, cc, nc)
                candidate = _candidate_offset_point(cc, direction, offset_m, forbidden)

                if candidate is None:
                    ring_ids.append(None)
                    continue

                key = (round(candidate.x, 2), round(candidate.y, 2))

                if key in seen:
                    # Gleiche Position wie ein bereits erzeugter Knoten → wiederverwenden.
                    ring_ids.append(seen[key])
                else:
                    node_id      = len(nodes)
                    seen[key]    = node_id
                    ring_ids.append(node_id)
                    nodes.append({
                        "node_id":       node_id,
                        "node_kind":     "zone",
                        "label":         "Zone",
                        "grid_row":      None,
                        "grid_col":      None,
                        "x":             candidate.x,
                        "y":             candidate.y,
                        "spacing_m":     None,
                        "zone_offset_m": offset_m,
                        "geometry":      candidate,
                    })

            rings.append(ring_ids)

    return nodes, rings


# =============================================================================
# Schritt 2: Ring-Kanten erzeugen (benachbarte Ecken desselben Polygons)
# =============================================================================

def _build_ring_edges(rings, node_by_id, forbidden):
    """
    Verbindet jeweils benachbarte Ecken desselben Polygon-Rings.

    Diese Kanten bilden die Zonenrand-Traversierung: eine Drohne kann
    immer um eine Sperrzone herumfliegen, indem sie von Eckknoten zu
    Eckknoten entlanggeht.

    Kanten, deren Verbindungslinie eine Sperrzone schneidet (z. B. bei
    sehr konkaven Polygonen), werden verworfen.

    Rückgabe:
        (edges, connected_pairs)
        edges:           Liste von Kanten-Dicts
        connected_pairs: Set von frozenset({from_id, to_id}) – für die
                         spätere Duplikats-Vermeidung
    """
    edges           = []
    connected_pairs = set()

    for ring_ids in rings:
        n = len(ring_ids)

        for i in range(n):
            from_id = ring_ids[i]
            to_id   = ring_ids[(i + 1) % n]

            if from_id is None or to_id is None or from_id == to_id:
                continue

            pair = frozenset((from_id, to_id))
            if pair in connected_pairs:
                continue

            from_pt = node_by_id[from_id]["geometry"]
            to_pt   = node_by_id[to_id]["geometry"]
            line    = LineString([from_pt, to_pt])

            if forbidden.intersects(line):
                continue

            edges.append({
                "edge_id":          len(edges),
                "from_node":        from_id,
                "to_node":          to_id,
                "length_m":         line.length,
                "spacing_m":        None,
                "diagonal":         None,
                "connect_diagonal": None,
                "edge_kind":        "ring",
                "orientation":      "ring",
                "geometry":         line,
            })
            connected_pairs.add(pair)

    return edges, connected_pairs


# =============================================================================
# Schritt 3: Sichtbarkeitskanten erzeugen (kreuzungsfrei)
# =============================================================================

def _build_visibility_edges(nodes, forbidden, forbidden_area, existing_edges, connected_pairs, *, max_distance_m):
    """
    Fügt Sichtbarkeitskanten zwischen allen Knotenpaaren hinzu, die noch
    nicht durch Ring-Kanten verbunden sind.

    Vorgehen:
    1. Kandidaten-Scan (O(N²)):
       Entfernungsfilter direkt auf Koordinaten (kein LineString-Objekt nötig).
    2. Batch-Erzeugung und Zonenschnitt (vektorisiert):
       Alle verbliebenen Linien werden per shapely.linestrings() auf GEOS-Ebene
       erzeugt; shapely.intersects(arr, forbidden_area) prüft den Zonenschnitt
       für alle auf einmal – ohne Python-Overhead pro Aufruf.
    3. Greedy-Auswahl mit STRtree (O(C · log E) statt O(C · E)):
       Ein STRtree wird aus den bereits akzeptierten Kanten gebaut und mit
       predicate='crosses' abgefragt. Neu akzeptierte Kanten sammeln sich in
       einer Pending-Liste; alle REBUILD_EVERY Einträge wird der Baum neu gebaut.

    forbidden_area:
        Rohe Shapely-Geometrie der vereinigten Sperrzonen (für den Batch-Test).

    Rückgabe:
        Liste neuer Kanten-Dicts (edge_id fortlaufend nach existing_edges).
    """
    REBUILD_EVERY = 100

    # --- Phase 1: Kandidatenpaare sammeln ---
    # Entfernung aus Koordinaten berechnen – kein LineString-Objekt nötig.

    raw = []   # (dist, from_id, to_id, from_pt, to_pt)

    for i in range(len(nodes)):
        from_pt = nodes[i]["geometry"]
        from_id = nodes[i]["node_id"]

        for j in range(i + 1, len(nodes)):
            to_id = nodes[j]["node_id"]

            if frozenset((from_id, to_id)) in connected_pairs:
                continue

            to_pt = nodes[j]["geometry"]
            dx    = to_pt.x - from_pt.x
            dy    = to_pt.y - from_pt.y
            dist  = sqrt(dx * dx + dy * dy)

            if max_distance_m is not None and dist > max_distance_m:
                continue

            raw.append((dist, from_id, to_id, from_pt, to_pt))

    if not raw:
        return []

    # --- Phase 2: Linien vektorisiert erzeugen + Zonenschnitt im Batch ---

    n      = len(raw)
    all_xy = np.empty((2 * n, 2))

    for k, (_, _, _, fp, tp) in enumerate(raw):
        all_xy[2 * k]     = (fp.x, fp.y)
        all_xy[2 * k + 1] = (tp.x, tp.y)

    lines_arr = shapely.linestrings(all_xy, indices=np.repeat(np.arange(n), 2))
    zone_hit  = shapely.intersects(lines_arr, forbidden_area)

    # --- Phase 3: Kandidaten sortieren (kürzeste zuerst) ---

    candidates = sorted(
        [(raw[k][0], raw[k][1], raw[k][2], lines_arr[k])
         for k in range(n) if not zone_hit[k]]
    )

    # --- Phase 4: Greedy-Auswahl mit STRtree-Kreuzungsfilter ---

    # Ring-Kanten bilden den initialen Bauminhalt.
    tree_geoms    = [e["geometry"] for e in existing_edges]
    pending_geoms = []
    tree          = STRtree(tree_geoms) if tree_geoms else None

    new_edges = []
    base_id   = len(existing_edges)

    for length_m, from_id, to_id, line in candidates:
        # Baum-Query: O(log E + k), alle echten Kreuzungen auf GEOS-Ebene.
        # crosses() ignoriert gemeinsame Endpunkte automatisch (DE-9IM).
        if tree is not None and len(tree.query(line, predicate="crosses")) > 0:
            continue

        # Pending-Liste: Kanten seit dem letzten Rebuild (max REBUILD_EVERY Stück).
        if any(line.crosses(g) for g in pending_geoms):
            continue

        new_edges.append({
            "edge_id":          base_id + len(new_edges),
            "from_node":        from_id,
            "to_node":          to_id,
            "length_m":         length_m,
            "spacing_m":        None,
            "diagonal":         None,
            "connect_diagonal": None,
            "edge_kind":        "visibility",
            "orientation":      "visibility",
            "geometry":         line,
        })
        pending_geoms.append(line)

        if len(pending_geoms) >= REBUILD_EVERY:
            tree_geoms.extend(pending_geoms)
            tree          = STRtree(tree_geoms)
            pending_geoms = []

    return new_edges


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
    max_edge_distance_m=None,
    start_lat=None,
    start_lon=None,
    end_lat=None,
    end_lon=None,
):
    """
    Erstellt einen kreuzungsfreien Sichtbarkeitsgraphen um LuftVO-Sperrzonen.

    Schritte:
    1. Knoten an den Außenecken jeder Sperrzone (offset_m außerhalb).
    2. Ring-Kanten: benachbarte Ecken desselben Polygons verbinden.
    3. Sichtbarkeitskanten: kürzeste kreuzungsfreie Verbindungen zwischen
       allen Knoten (inkl. Start/End) hinzufügen.
    4. Ausgabe als GeoJSON.

    Kanten dürfen sich nie kreuzen (kreuzungsfreie Bedingung ist immer aktiv).

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

    # --- Sperrzonen laden, reparieren und vereinigen ---

    zones = gpd.read_file(zones_path)

    if zones.empty:
        raise ValueError("Die Zonen-Datei enthält keine Features.")

    zones        = zones.set_crs(WGS84) if zones.crs is None else zones.to_crs(WGS84)
    zones_metric = zones.to_crs(metric_crs)
    zones_metric["geometry"] = zones_metric.geometry.buffer(0)   # Topologie-Fehler beheben

    forbidden_area = get_geometry_union(zones_metric)

    # Einmalige Vorberechnung der Sperrzone für schnelle wiederholte Tests.
    forbidden = prepare_geometry(forbidden_area)

    # ==========================================================================
    # Schritt 1: Knoten und Ring-Zugehörigkeit erzeugen
    # ==========================================================================

    nodes, rings = _create_nodes_and_rings(
        zones_metric=zones_metric,
        forbidden=forbidden,
        offset_m=node_offset_m,
    )

    if not nodes:
        raise ValueError("Es konnten keine Knoten aus Zonenecken erzeugt werden.")

    # ==========================================================================
    # Schritt 2: Start- und Endknoten hinzufügen
    # ==========================================================================
    # Start/End werden VOR der Sichtbarkeitsphase eingefügt, sodass sie
    # im selben Durchlauf wie alle anderen Knoten verbunden werden.

    if start_lat is not None and start_lon is not None:
        pt = wgs84_to_metric(start_lat, start_lon, metric_crs)

        if forbidden.intersects(pt):
            raise ValueError("Startpunkt liegt innerhalb oder auf einer Sperrzone.")

        nodes.append({
            "node_id":       len(nodes),
            "node_kind":     "start",
            "label":         "Start",
            "grid_row":      None,
            "grid_col":      None,
            "x":             pt.x,
            "y":             pt.y,
            "spacing_m":     None,
            "zone_offset_m": None,
            "geometry":      pt,
        })

    if end_lat is not None and end_lon is not None:
        pt = wgs84_to_metric(end_lat, end_lon, metric_crs)

        if forbidden.intersects(pt):
            raise ValueError("Endpunkt liegt innerhalb oder auf einer Sperrzone.")

        nodes.append({
            "node_id":       len(nodes),
            "node_kind":     "end",
            "label":         "Ende",
            "grid_row":      None,
            "grid_col":      None,
            "x":             pt.x,
            "y":             pt.y,
            "spacing_m":     None,
            "zone_offset_m": None,
            "geometry":      pt,
        })

    node_by_id = {n["node_id"]: n for n in nodes}

    # ==========================================================================
    # Schritt 3a: Ring-Kanten erzeugen (Zonenrand-Traversierung)
    # ==========================================================================

    ring_edges, connected_pairs = _build_ring_edges(rings, node_by_id, forbidden)

    # ==========================================================================
    # Schritt 3b: Sichtbarkeitskanten erzeugen (inkl. Start/End-Anbindung)
    # ==========================================================================

    vis_edges = _build_visibility_edges(
        nodes=nodes,
        forbidden=forbidden,
        forbidden_area=forbidden_area,
        existing_edges=ring_edges,
        connected_pairs=connected_pairs,
        max_distance_m=max_edge_distance_m,
    )

    all_edges = ring_edges + vis_edges

    if not all_edges:
        raise ValueError("Es wurden keine erlaubten Kanten erzeugt.")

    # Sicherstellen, dass Start und End mindestens eine Verbindung haben.
    for sp_kind in ("start", "end"):
        sp_list = [n for n in nodes if n["node_kind"] == sp_kind]
        for sp in sp_list:
            sid = sp["node_id"]
            if not any(e["from_node"] == sid or e["to_node"] == sid for e in all_edges):
                raise ValueError(
                    f"{sp['label']} hat keine Verbindung zum Graphen. "
                    f"Vergrößern Sie ZONEN_MAX_KANTENLAENGE_M oder prüfen Sie den Standort."
                )

    # ==========================================================================
    # Schritt 4: Als GeoJSON speichern
    # ==========================================================================

    nodes_gdf = gpd.GeoDataFrame(nodes, geometry="geometry", crs=metric_crs)
    edges_gdf = gpd.GeoDataFrame(all_edges, geometry="geometry", crs=metric_crs)

    nodes_path.parent.mkdir(parents=True, exist_ok=True)
    edges_path.parent.mkdir(parents=True, exist_ok=True)

    nodes_wgs84 = nodes_gdf.to_crs(WGS84)
    edges_wgs84 = edges_gdf.to_crs(WGS84)

    nodes_wgs84.to_file(nodes_path, driver="GeoJSON")
    edges_wgs84.to_file(edges_path, driver="GeoJSON")

    return nodes_wgs84, edges_wgs84
