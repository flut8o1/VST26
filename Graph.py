"""
Graph.py – Erzeugung eines Gitter-Navigationsgraphen als Matrix.

Statt explizite Knoten- und Kantenlisten zu führen, wird das regelmäßige
Gitter als Matrix dargestellt:

  - node_valid[row, col]            – ist der Gitterknoten erlaubt?
  - passable[richtung][row, col]    – ist die Kante in diese Richtung frei?

Diese Darstellung macht die Wegsuche einfach und schnell: Die Algorithmen
indexieren direkt in die Arrays, statt eine Adjazenzliste aufzubauen.

Knoten innerhalb von LuftVO-Sperrzonen werden entfernt, Kanten, die eine
Sperrzone schneiden, ebenfalls. Beide Tests laufen vektorisiert über einen
räumlichen Join (sjoin), nicht über eine Python-Schleife.

Start- und Endpunkt liegen nicht auf dem Gitter und werden als Sonderknoten
außerhalb der Matrix geführt; ihre Verbindungen zu den nächsten sichtbaren
Gitterknoten werden separat gespeichert.
"""

import math
from dataclasses import dataclass

import numpy as np
import geopandas as gpd
from shapely.geometry import LineString

from utils import WGS84, DEFAULT_METRIC_CRS, get_geometry_union, wgs84_to_metric, prepare_geometry


# Richtungs-Offsets (dr, dc): dr entlang der Zeilen (Norden positiv),
# dc entlang der Spalten (Osten positiv).
_ORTHO = {"E": (0, 1), "W": (0, -1), "N": (1, 0), "S": (-1, 0)}

# Zu jeder getesteten Vorwärtsrichtung die entgegengesetzte Richtung.
_REVERSE = {"E": "W", "N": "S"}


# =============================================================================
# Datenstruktur
# =============================================================================

@dataclass
class NavigationGrid:
    """
    Matrix-Darstellung des Navigationsgitters.

    Geometrie-Raster:
        minx, miny  – Ursprung (untere linke Ecke) im metrischen CRS.
        spacing_m   – Knotenabstand in Metern.
        n_rows, n_cols, metric_crs

    Matrix:
        node_valid  – (n_rows, n_cols) bool: erlaubter Gitterknoten?
        passable    – Richtung -> (n_rows, n_cols) bool: Kante frei?

    Start/Ende (im metrischen CRS):
        start_xy, end_xy            – Koordinaten der Sonderknoten.
        start_connections           – [(grid_node_id, length_m), ...]
        end_connections             – dito für den Endknoten.

    Zeichengeometrie / Kennzahlen:
        nodes_gdf, edges_gdf        – GeoDataFrames (WGS84) zum Zeichnen.
        total_edge_length_m         – Gesamtlänge aller Kanten in Metern.

    Knoten-IDs:
        Gitterknoten (row, col) -> row * n_cols + col
        Startknoten -> n_rows * n_cols
        Endknoten   -> n_rows * n_cols + 1
    """
    minx: float
    miny: float
    spacing_m: float
    n_rows: int
    n_cols: int
    metric_crs: str
    node_valid: np.ndarray
    passable: dict
    start_xy: tuple
    end_xy: tuple
    start_connections: list
    end_connections: list
    total_edge_length_m: float
    nodes_gdf: gpd.GeoDataFrame
    edges_gdf: gpd.GeoDataFrame

    @property
    def start_id(self):
        return self.n_rows * self.n_cols

    @property
    def end_id(self):
        return self.n_rows * self.n_cols + 1

    def node_xy(self, node_id):
        """Metrische Koordinate eines Knotens (Gitter, Start oder Ende)."""
        if node_id == self.start_id:
            return self.start_xy
        if node_id == self.end_id:
            return self.end_xy
        row, col = divmod(node_id, self.n_cols)
        return (self.minx + col * self.spacing_m, self.miny + row * self.spacing_m)


# =============================================================================
# Knoten- und Kanten-Tests (vektorisiert)
# =============================================================================

def _compute_node_valid(minx, miny, spacing_m, n_rows, n_cols, metric_crs, zones_metric):
    """
    Bestimmt für jede Gitterzelle, ob der Knoten außerhalb aller Sperrzonen liegt.

    Erzeugt alle Gitterpunkte auf einmal und ermittelt per räumlichem Join,
    welche in einer Sperrzone liegen. Rückgabe: (n_rows, n_cols) bool-Array.
    """
    cols, rows = np.meshgrid(np.arange(n_cols), np.arange(n_rows))
    xs = (minx + cols * spacing_m).ravel()
    ys = (miny + rows * spacing_m).ravel()

    points = gpd.GeoDataFrame(geometry=gpd.points_from_xy(xs, ys), crs=metric_crs)
    inside = points.sjoin(zones_metric[["geometry"]], predicate="intersects", how="inner")

    valid = np.ones(n_rows * n_cols, dtype=bool)
    valid[np.asarray(inside.index.unique(), dtype=int)] = False

    return valid.reshape(n_rows, n_cols)


def _test_direction(node_valid, dr, dc, minx, miny, spacing_m, metric_crs, zones_metric):
    """
    Prüft alle Kanten einer Richtung (dr, dc) gemeinsam.

    Eine Kante ist nur dann frei, wenn beide Endknoten erlaubt sind und die
    Verbindungslinie keine Sperrzone schneidet. Der Schnitt-Test läuft
    vektorisiert über einen räumlichen Join.

    Rückgabe:
        forward     – (n_rows, n_cols) bool: freie Kante ab Quellknoten (row, col).
        kept_lines  – Liste der freien Kanten als LineString (zum Zeichnen).
        length_m    – Gesamtlänge der freien Kanten dieser Richtung.
    """
    n_rows, n_cols = node_valid.shape

    # Quell- und Zielbereich als Slices (dr ist 0 oder 1, dc ist -1, 0 oder 1).
    src_r = slice(0, n_rows - dr) if dr == 1 else slice(0, n_rows)
    dst_r = slice(dr, n_rows)     if dr == 1 else slice(0, n_rows)

    if dc == 1:
        src_c, dst_c = slice(0, n_cols - 1), slice(1, n_cols)
    elif dc == -1:
        src_c, dst_c = slice(1, n_cols), slice(0, n_cols - 1)
    else:
        src_c, dst_c = slice(0, n_cols), slice(0, n_cols)

    forward = np.zeros((n_rows, n_cols), dtype=bool)

    # Kandidaten: beide Endknoten erlaubt.
    candidates = node_valid[src_r, src_c] & node_valid[dst_r, dst_c]
    rows_local, cols_local = np.nonzero(candidates)

    if rows_local.size == 0:
        return forward, [], 0.0

    src_rows = rows_local + src_r.start
    src_cols = cols_local + src_c.start

    x0 = minx + src_cols * spacing_m
    y0 = miny + src_rows * spacing_m
    x1 = minx + (src_cols + dc) * spacing_m
    y1 = miny + (src_rows + dr) * spacing_m

    lines = [LineString([(x0[i], y0[i]), (x1[i], y1[i])]) for i in range(rows_local.size)]

    # Welche Kanten schneiden eine Sperrzone?
    edges = gpd.GeoDataFrame(geometry=lines, crs=metric_crs)
    blocked = edges.sjoin(zones_metric[["geometry"]], predicate="intersects", how="inner")

    blocked_mask = np.zeros(len(lines), dtype=bool)
    blocked_mask[np.asarray(blocked.index.unique(), dtype=int)] = True
    keep = ~blocked_mask

    forward[src_rows[keep], src_cols[keep]] = True

    kept_lines = [lines[i] for i in np.nonzero(keep)[0]]
    edge_length = spacing_m * math.sqrt(dr * dr + dc * dc)

    return forward, kept_lines, len(kept_lines) * edge_length


def _set_reverse(passable, name, dr, dc, forward):
    """
    Trägt die freie Vorwärtskante auch als Rückwärtskante in die Matrix ein.

    Eine Kante (r, c) -> (r+dr, c+dc) ist in beide Richtungen passierbar.
    Die Gegenrichtung wird durch Verschieben des forward-Arrays um (dr, dc)
    eingetragen.
    """
    reverse = passable[_REVERSE[name]]
    n_rows, n_cols = forward.shape

    dst_r = slice(dr, n_rows)     if dr == 1 else slice(0, n_rows)
    src_r = slice(0, n_rows - dr) if dr == 1 else slice(0, n_rows)

    if dc == 1:
        dst_c, src_c = slice(1, n_cols), slice(0, n_cols - 1)
    elif dc == -1:
        dst_c, src_c = slice(0, n_cols - 1), slice(1, n_cols)
    else:
        dst_c, src_c = slice(0, n_cols), slice(0, n_cols)

    reverse[dst_r, dst_c] = forward[src_r, src_c]


def _compute_edges(node_valid, minx, miny, spacing_m, metric_crs, zones_metric):
    """
    Baut die Passierbarkeits-Matrix für alle Richtungen auf.

    Es werden nur die Vorwärtsrichtungen geometrisch getestet; die jeweilige
    Gegenrichtung wird daraus abgeleitet.

    Rückgabe:
        passable    – Richtung -> (n_rows, n_cols) bool.
        edge_lines  – Liste aller freien Kanten als LineString (zum Zeichnen).
        total_len   – Gesamtlänge aller Kanten in Metern.
    """
    n_rows, n_cols = node_valid.shape
    passable = {d: np.zeros((n_rows, n_cols), dtype=bool) for d in _ORTHO}

    edge_lines = []
    total_len  = 0.0

    for name, dr, dc in [("E", 0, 1), ("N", 1, 0)]:
        forward, lines, length = _test_direction(
            node_valid, dr, dc, minx, miny, spacing_m, metric_crs, zones_metric
        )
        passable[name] = forward
        _set_reverse(passable, name, dr, dc, forward)
        edge_lines.extend(lines)
        total_len += length

    return passable, edge_lines, total_len


def _connect_special(point, node_valid, minx, miny, spacing_m, n_cols,
                     forbidden, max_connections, label):
    """
    Verbindet einen Start- oder Endpunkt mit den nächsten sichtbaren Gitterknoten.

    Es werden bis zu max_connections Verbindungen erstellt, sortiert nach
    aufsteigender Entfernung. Verbindungen, die eine Sperrzone schneiden,
    werden übersprungen.

    Rückgabe:
        connections – [(grid_node_id, length_m), ...]
        lines       – Verbindungslinien als LineString (zum Zeichnen).
    """
    rows, cols = np.nonzero(node_valid)
    xs = minx + cols * spacing_m
    ys = miny + rows * spacing_m

    distances = np.hypot(xs - point.x, ys - point.y)
    order = np.argsort(distances)

    connections = []
    lines       = []

    for idx in order:
        if distances[idx] <= 0:
            continue

        line = LineString([(point.x, point.y), (xs[idx], ys[idx])])

        # Verbindung überspringen, wenn sie eine Sperrzone schneidet.
        if forbidden.intersects(line):
            continue

        node_id = int(rows[idx]) * n_cols + int(cols[idx])
        connections.append((node_id, float(distances[idx])))
        lines.append(line)

        if len(connections) >= max_connections:
            break

    return connections, lines


def _build_nodes_gdf(node_valid, minx, miny, spacing_m, metric_crs):
    """Erzeugt ein GeoDataFrame (WGS84) aller erlaubten Gitterknoten zum Zeichnen."""
    rows, cols = np.nonzero(node_valid)
    xs = minx + cols * spacing_m
    ys = miny + rows * spacing_m

    gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy(xs, ys), crs=metric_crs)
    return gdf.to_crs(WGS84)


# =============================================================================
# Haupt-Pipeline-Funktion
# =============================================================================

def create_navigation_graph(
    zones,
    *,
    spacing_m=250,
    metric_crs=DEFAULT_METRIC_CRS,
    bbox_padding_m=0,
    max_bbox_wgs84=None,
    start_lat=None,
    start_lon=None,
    end_lat=None,
    end_lon=None,
    special_connections_per_point=1,
):
    """
    Erzeugt das Navigationsgitter als Matrix.

    zones:
        GeoDataFrame der LuftVO-Sperrzonen (wie von Map_Preprocessing erzeugt).

    Regeln:
    - Gitterknoten liegen im gleichmäßigen Abstand spacing_m.
    - Knoten innerhalb oder auf Sperrzonen werden entfernt.
    - Kanten, die Sperrzonen schneiden, werden entfernt.
    - Nur West-Ost- und Nord-Süd-Verbindungen (keine Diagonalen).
    - Start- und Endpunkt werden als Sonderknoten geführt und mit den
      nächsten sichtbaren Gitterknoten verbunden.

    Rückgabe:
        NavigationGrid – Matrix-Darstellung samt Zeichengeometrie.
    """
    if zones.empty:
        raise ValueError("Die Zonen-Daten enthalten keine Features.")

    # --- Sperrzonen reparieren und zu einer Gesamtfläche vereinigen ---

    zones        = zones.set_crs(WGS84) if zones.crs is None else zones.to_crs(WGS84)
    zones_metric = zones.to_crs(metric_crs).copy()
    zones_metric["geometry"] = zones_metric.geometry.buffer(0)   # Topologie-Fehler beheben

    # Vorbereitete Gesamtfläche für die wenigen Start/End-Verbindungstests.
    forbidden = prepare_geometry(get_geometry_union(zones_metric))

    # --- Start- und Endpunkt vorbereiten und validieren ---

    start_pt = wgs84_to_metric(start_lat, start_lon, metric_crs)
    end_pt   = wgs84_to_metric(end_lat, end_lon, metric_crs)

    for label, pt in (("Start", start_pt), ("Ende", end_pt)):
        if forbidden.intersects(pt):
            raise ValueError(f"{label} liegt innerhalb oder auf einer Sperrzone.")

    # --- Bounding Box bestimmen (Zonen + Start/End + optionales Padding) ---

    minx, miny, maxx, maxy = zones_metric.total_bounds

    for pt in (start_pt, end_pt):
        minx = min(minx, pt.x)
        miny = min(miny, pt.y)
        maxx = max(maxx, pt.x)
        maxy = max(maxy, pt.y)

    minx -= bbox_padding_m
    miny -= bbox_padding_m
    maxx += bbox_padding_m
    maxy += bbox_padding_m

    # Optional: Gitter auf den sichtbaren PNG-Ausschnitt begrenzen.
    if max_bbox_wgs84 is not None:
        sw = wgs84_to_metric(max_bbox_wgs84[0], max_bbox_wgs84[1], metric_crs)
        ne = wgs84_to_metric(max_bbox_wgs84[2], max_bbox_wgs84[3], metric_crs)
        minx = max(minx, sw.x)
        miny = max(miny, sw.y)
        maxx = min(maxx, ne.x)
        maxy = min(maxy, ne.y)

    spacing_m = float(spacing_m)
    n_cols = int((maxx - minx) // spacing_m) + 1
    n_rows = int((maxy - miny) // spacing_m) + 1

    # --- Matrix aufbauen: erlaubte Knoten, dann freie Kanten ---

    node_valid = _compute_node_valid(minx, miny, spacing_m, n_rows, n_cols, metric_crs, zones_metric)

    passable, edge_lines, total_len = _compute_edges(
        node_valid, minx, miny, spacing_m, metric_crs, zones_metric
    )

    # --- Start/Ende mit den nächsten sichtbaren Gitterknoten verbinden ---

    start_connections, start_lines = _connect_special(
        start_pt, node_valid, minx, miny, spacing_m, n_cols,
        forbidden, special_connections_per_point, "Start",
    )
    end_connections, end_lines = _connect_special(
        end_pt, node_valid, minx, miny, spacing_m, n_cols,
        forbidden, special_connections_per_point, "Ende",
    )

    total_len += sum(length for _, length in start_connections)
    total_len += sum(length for _, length in end_connections)

    # --- Zeichengeometrie als GeoDataFrames (WGS84) ---

    nodes_gdf = _build_nodes_gdf(node_valid, minx, miny, spacing_m, metric_crs)
    edges_gdf = gpd.GeoDataFrame(
        geometry=edge_lines + start_lines + end_lines, crs=metric_crs
    ).to_crs(WGS84)

    return NavigationGrid(
        minx=minx,
        miny=miny,
        spacing_m=spacing_m,
        n_rows=n_rows,
        n_cols=n_cols,
        metric_crs=metric_crs,
        node_valid=node_valid,
        passable=passable,
        start_xy=(start_pt.x, start_pt.y),
        end_xy=(end_pt.x, end_pt.y),
        start_connections=start_connections,
        end_connections=end_connections,
        total_edge_length_m=total_len,
        nodes_gdf=nodes_gdf,
        edges_gdf=edges_gdf,
    )
