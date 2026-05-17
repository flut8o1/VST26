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
    Schritt 3 ist der dominante Aufwand. Er ist vollständig vektorisiert
    und teilweise parallelisiert:
    - Phase 1 (Kandidaten): STRtree dwithin-Query liefert nur Paare
      innerhalb max_distance_m – O(N log N), kein O(N²)-Speicher-Blowup.
    - Phase 2 (Linien + Zonenschnitt): shapely.linestrings() erzeugt alle
      Kandidaten-Linien in einem GEOS-Batch; shapely.prepare() beschleunigt
      die Intersects-Tests; bei >50 000 Kandidaten werden die Tests in
      _N_WORKERS Threads parallelisiert (GEOS gibt GIL frei).
    - Phase 3 (Konflikt-Graph): STRtree.query(all_lines, predicate='crosses')
      liefert ALLE sich kreuzenden Paare in einem einzigen GEOS-Batch-Aufruf;
      die Adjazenzliste wird vektorisiert mit numpy-argsort/split gebaut
      (statt Python-Schleife).
    - Phase 4 (Greedy): Reine Python-Auswahl ohne weitere GEOS-Calls.
    Knoten und Ring-Kanten profitieren weiterhin von prep() (PreparedGeometry).
"""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from math import sqrt, cos, sin, pi

import numpy as np
import shapely
import geopandas as gpd
from shapely import STRtree
from shapely.geometry import Point, LineString
from shapely.geometry.polygon import orient

# Parallelitätsgrad für Phase-2-Threading (GEOS gibt GIL frei).
_N_WORKERS = min(os.cpu_count() or 1, 8)

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

def _build_visibility_edges(nodes, forbidden_area, existing_edges, connected_pairs, *, max_distance_m, crossing_free=True):
    """
    Fügt Sichtbarkeitskanten zwischen allen Knotenpaaren hinzu, die noch
    nicht durch Ring-Kanten verbunden sind.

    Vorgehen (vollständig vektorisiert):
    1. Kandidaten via STRtree dwithin:
       Nur Paare innerhalb max_distance_m werden erzeugt – O(N log N),
       kein O(N²)-Speicher-Blowup. Ohne Distanzlimit: Fehler wenn N zu groß.
    2. Batch-LineStrings + Zonenschnitt:
       shapely.linestrings(coords, indices=...) erzeugt alle Linien in
       einem GEOS-Aufruf; shapely.intersects(lines, forbidden_area)
       filtert Zonenschneider in einem Batch.
    3. Konflikt-Graph in einem einzigen Batch-Aufruf:
       STRtree.query(all_lines, predicate='crosses') liefert ALLE
       sich kreuzenden Paare auf einmal – statt N Einzel-Queries.
       Resultat: pro Linie eine Adjazenzliste mit ihren Kreuzungs-
       Konflikten.
    4. Greedy-Auswahl (Python-only, kein GEOS):
       Kürzeste Kanten zuerst; eine Kante wird akzeptiert, wenn keiner
       ihrer Konflikte bereits akzeptiert wurde. Ring-Kanten gelten als
       von Anfang an akzeptiert.

    crossing_free:
        Wenn True (Standard), wird der kreuzungsfreie Greedy-Filter
        angewendet (Phasen 3+4). Wenn False, werden ALLE sichtbaren
        (zonen-freien) Kandidaten als Kanten ausgegeben – dichter
        Sichtbarkeitsgraph mit Kreuzungen.

    forbidden_area:
        Rohe Shapely-Geometrie der vereinigten Sperrzonen (Batch-Test).

    Rückgabe:
        Liste neuer Kanten-Dicts (edge_id fortlaufend nach existing_edges).
    """
    n_total = len(nodes)
    if n_total < 2:
        return []

    # --- Phase 1: Vektorisierte Kandidaten-Erzeugung ----------------------

    xs       = np.fromiter((nd["x"]       for nd in nodes), dtype=np.float64, count=n_total)
    ys       = np.fromiter((nd["y"]       for nd in nodes), dtype=np.float64, count=n_total)
    node_ids = np.fromiter((nd["node_id"] for nd in nodes), dtype=np.int64,   count=n_total)

    if max_distance_m is not None:
        # O(N log N): STRtree dwithin – erzeugt nur Paare ≤ max_distance_m,
        # kein O(N²)-Speicher-Blowup bei großen Graphen.
        pts     = shapely.points(np.column_stack([xs, ys]))
        pt_tree = STRtree(pts)
        raw     = pt_tree.query(pts, predicate="dwithin", distance=max_distance_m)
        upper   = raw[0] < raw[1]          # i < j, keine Selbst-/Doppelpaare
        i_arr   = raw[0][upper]
        j_arr   = raw[1][upper]
        dx      = xs[j_arr] - xs[i_arr]
        dy      = ys[j_arr] - ys[i_arr]
        dists   = np.sqrt(dx * dx + dy * dy)
    else:
        # O(N²): nur für kleine Graphen geeignet
        n_pairs = n_total * (n_total - 1) // 2
        if n_pairs > 20_000_000:
            raise MemoryError(
                f"Zu viele Knotenpaare ({n_pairs:,}) bei unbegrenzter Kantenlänge. "
                f"Bitte ZONEN_MAX_KANTENLAENGE_M setzen."
            )
        i_arr, j_arr = np.triu_indices(n_total, k=1)
        dx    = xs[j_arr] - xs[i_arr]
        dy    = ys[j_arr] - ys[i_arr]
        dists = np.sqrt(dx * dx + dy * dy)

    # Filter: Paare, die schon durch Ring-Kanten verbunden sind.
    if connected_pairs:
        n_max = int(node_ids.max()) + 1
        cm    = np.zeros((n_max, n_max), dtype=bool)
        for fs in connected_pairs:
            items = list(fs)
            if len(items) == 2:
                a, b      = int(items[0]), int(items[1])
                cm[a, b]  = True
                cm[b, a]  = True

        m     = ~cm[node_ids[i_arr], node_ids[j_arr]]
        i_arr = i_arr[m]
        j_arr = j_arr[m]
        dists = dists[m]

    if len(i_arr) == 0:
        return []

    # --- Phase 2: Batch-LineStrings + Zonenschnitt (parallelisiert) -------

    n_cand            = len(i_arr)
    coords            = np.empty((2 * n_cand, 2), dtype=np.float64)
    coords[0::2, 0]   = xs[i_arr]
    coords[0::2, 1]   = ys[i_arr]
    coords[1::2, 0]   = xs[j_arr]
    coords[1::2, 1]   = ys[j_arr]

    cand_lines = shapely.linestrings(coords, indices=np.repeat(np.arange(n_cand), 2))

    # PreparedGeometry macht jeden Einzel-Test ca. 2–10× schneller.
    # GEOS gibt die GIL während der Intersects-Berechnung frei → echter
    # Parallel-Speedup mit ThreadPoolExecutor auf Multi-Core-Systemen.
    shapely.prepare(forbidden_area)
    try:
        if n_cand > 50_000 and _N_WORKERS > 1:
            chunks   = np.array_split(cand_lines, _N_WORKERS)
            with ThreadPoolExecutor(max_workers=_N_WORKERS) as pool:
                zone_hit = np.concatenate(list(pool.map(
                    lambda ch: shapely.intersects(ch, forbidden_area), chunks,
                )))
        else:
            zone_hit = shapely.intersects(cand_lines, forbidden_area)
    finally:
        shapely.destroy_prepared(forbidden_area)

    keep       = ~zone_hit

    cand_lines = cand_lines[keep]
    i_arr      = i_arr[keep]
    j_arr      = j_arr[keep]
    dists      = dists[keep]

    if len(cand_lines) == 0:
        return []

    # --- Shortcut: alle sichtbaren Kanten ohne Kreuzungs-Filter -----------
    # Wenn crossing_free=False ist, werden ALLE zonen-freien Kandidaten als
    # Kanten ausgegeben (dichter Sichtbarkeitsgraph mit Kreuzungen).
    if not crossing_free:
        base_id   = len(existing_edges)
        new_edges = []
        for k in range(len(cand_lines)):
            new_edges.append({
                "edge_id":          base_id + k,
                "from_node":        int(node_ids[i_arr[k]]),
                "to_node":          int(node_ids[j_arr[k]]),
                "length_m":         float(dists[k]),
                "spacing_m":        None,
                "diagonal":         None,
                "connect_diagonal": None,
                "edge_kind":        "visibility",
                "orientation":      "visibility",
                "geometry":         cand_lines[k],
            })
        return new_edges

    # --- Phase 3: Konflikt-Graph in einem Batch-Query ---------------------

    ring_geoms = [e["geometry"] for e in existing_edges]
    n_rings    = len(ring_geoms)

    if n_rings > 0:
        all_lines = np.concatenate([np.array(ring_geoms, dtype=object), cand_lines])
    else:
        all_lines = cand_lines

    n_all = len(all_lines)

    # Ein einziger STRtree-Aufruf liefert ALLE Kreuzungs-Paare. Das ersetzt
    # n_cand individuelle Queries (mit Python-Overhead je Aufruf) durch einen
    # einzigen GEOS-Batch-Aufruf, der intern den R-Baum effizient durchläuft.
    full_tree = STRtree(all_lines)
    pairs     = full_tree.query(all_lines, predicate="crosses")

    # Adjazenzliste der Konflikte pro Linie – vektorisiert mit numpy.
    # crosses() ist symmetrisch; der Tree-Query liefert beide Richtungen
    # (a→b und b→a), daher wird jede Richtung separat gespeichert.
    conflicts = [[] for _ in range(n_all)]
    if pairs.size > 0:
        a_row, b_row = pairs[0], pairs[1]
        mask = a_row != b_row
        if mask.any():
            a_fil    = a_row[mask]
            b_fil    = b_row[mask]
            sort_idx = np.argsort(a_fil, kind="stable")
            a_sorted = a_fil[sort_idx]
            b_sorted = b_fil[sort_idx]
            bounds   = np.flatnonzero(np.diff(a_sorted)) + 1
            for ga, gb in zip(np.split(a_sorted, bounds),
                              np.split(b_sorted, bounds)):
                conflicts[int(ga[0])] = gb.tolist()

    # --- Phase 4: Greedy-Auswahl rein in Python (kein GEOS mehr) ----------

    # Sichtbarkeits-Kandidaten nach Distanz aufsteigend sortieren.
    sort_order = np.argsort(dists, kind="stable")

    # Ring-Kanten haben Indizes 0..n_rings-1 und sind bereits akzeptiert.
    accepted = set(range(n_rings))

    new_edges = []
    base_id   = len(existing_edges)

    for k in sort_order:
        full_idx = n_rings + int(k)

        # Konflikt mit einer schon akzeptierten Kante? Dann verwerfen.
        clist = conflicts[full_idx]
        if clist and any(c in accepted for c in clist):
            continue

        accepted.add(full_idx)

        new_edges.append({
            "edge_id":          base_id + len(new_edges),
            "from_node":        int(node_ids[i_arr[k]]),
            "to_node":          int(node_ids[j_arr[k]]),
            "length_m":         float(dists[k]),
            "spacing_m":        None,
            "diagonal":         None,
            "connect_diagonal": None,
            "edge_kind":        "visibility",
            "orientation":      "visibility",
            "geometry":         cand_lines[k],
        })

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
    crossing_free=True,
    bbox_wgs84=None,
):
    """
    Erstellt einen Sichtbarkeitsgraphen um LuftVO-Sperrzonen.

    Schritte:
    1. Knoten an den Außenecken jeder Sperrzone (offset_m außerhalb).
    2. Ring-Kanten: benachbarte Ecken desselben Polygons verbinden.
    3. Sichtbarkeitskanten: Verbindungen zwischen allen Knoten (inkl.
       Start/End) hinzufügen. Modus per crossing_free wählbar:
         - True  (Default): kürzeste, kreuzungsfreie Greedy-Auswahl.
         - False: ALLE sichtbaren (zonen-freien) Kanten – dichter
                  Graph mit Kreuzungen.
    4. Ausgabe als GeoJSON.

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

    # Optional: Knoten auf den sichtbaren Bereich (PNG-Ausschnitt) begrenzen.
    # Start/End werden NACH dieser Filterung hinzugefügt und sind immer gültig.
    if bbox_wgs84 is not None:
        _sw = wgs84_to_metric(bbox_wgs84[0], bbox_wgs84[1], metric_crs)
        _ne = wgs84_to_metric(bbox_wgs84[2], bbox_wgs84[3], metric_crs)
        _valid = {
            nd["node_id"]
            for nd in nodes
            if _sw.x <= nd["x"] <= _ne.x and _sw.y <= nd["y"] <= _ne.y
        }
        nodes = [nd for nd in nodes if nd["node_id"] in _valid]
        rings = [
            [nid if (nid is None or nid in _valid) else None for nid in ring]
            for ring in rings
        ]
        if not nodes:
            raise ValueError(
                "Keine Zonenknoten innerhalb des PNG-Ausschnitts. "
                "PNG_SQUARE_SIDE_KM vergrößern oder PNG_CENTER_LAT/LON anpassen."
            )

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
        forbidden_area=forbidden_area,
        existing_edges=ring_edges,
        connected_pairs=connected_pairs,
        max_distance_m=max_edge_distance_m,
        crossing_free=crossing_free,
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
