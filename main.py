"""
main.py – Einstiegspunkt der Drohnen-Routenplanung (LuftVO-konform).

Gesamte Pipeline in vier Schritten:
    1. GeoJSON bearbeiten  – OSM-Daten klassifizieren und LuftVO-Zonen puffern.
    2. Graph erstellen     – Navigationsnetz (Gitter oder Sichtbarkeitsgraph) aufbauen.
    3. Wegsuche            – Kürzesten Weg mit Dijkstra oder A* finden.
    4. Karten (optional)   – Zonenkarte und/oder Graphkarte als PNG/HTML ausgeben.

Alle Einstellungen befinden sich ausschließlich in den Konfigurations-
abschnitten direkt unterhalb dieser Modulbeschreibung.
"""

from pathlib import Path
from time import perf_counter
from contextlib import redirect_stdout
from math import cos, radians
import io

from GeoJSON_Bearbeiten import create_luftvo_buffer_geojson
from geoJSON_viewer import create_geojson_visualization
from graph_erstellen import create_navigation_graph as create_grid_graph
from Graph_erstellen_Zonen import create_zone_visibility_graph
from graph_viewer import create_graph_png_map
from Wegfindungs import find_path_and_visualize


# =============================================================================
# Eingabe- und Ausgabedateien
# =============================================================================

# Eingabe: OSM-GeoJSON (z. B. aus Overpass-Abfrage)
INPUT_GEOJSON_NAME = "GeoDaten_ohne_Naturschutz.geojson"

# Ausgabe: gepufferte LuftVO-Sperrzonen
OUTPUT_ZONES_GEOJSON_NAME = "drohnen_luftvo_zonen.geojson"

# Ausgabe: Graphknoten und -kanten
OUTPUT_GRAPH_NODES_NAME = "graph_nodes.geojson"
OUTPUT_GRAPH_EDGES_NAME = "graph_edges.geojson"

# Ausgabe: gefundene Route
OUTPUT_ROUTE_NODES_NAME = "route_nodes.geojson"
OUTPUT_ROUTE_EDGES_NAME = "route_edges.geojson"

# Ausgabe: Karten als Dateien
OUTPUT_ZONES_MAP_HTML_NAME           = "drohnen_luftvo_karte.html"
OUTPUT_ZONES_MAP_PNG_NAME            = "drohnen_luftvo_karte.png"
OUTPUT_GRAPH_MAP_PNG_NAME            = "graph_karte.png"
OUTPUT_GRAPH_MAP_WITH_ZONES_PNG_NAME = "graph_karte_mit_zonen.png"
OUTPUT_ROUTE_MAP_PNG_NAME            = "route_karte.png"


# =============================================================================
# Karten-Ausgabeformat
# =============================================================================

# "png" oder "html" – gilt für die Zonenübersichtskarte
OUTPUT_FORMAT = "png"


# =============================================================================
# Zonenvorschaukarte
# =============================================================================

# True = Zonenübersichtskarte erzeugen
ZONEN_KARTE_ERSTELLEN = False

# True = Karte nach Erzeugung öffnen / anzeigen
ZONEN_KARTE_ANZEIGEN  = False


# =============================================================================
# Puffer-Einstellungen (GeoJSON_Bearbeiten.py)
# =============================================================================

# Anzahl der Ecken für Punkt-Polygone (höher = runder)
POLYGON_ECKEN_N = 8

# Rundheit der Puffer um bestehende Flächen/Linien (höher = runder)
ZONE_BUFFER_RESOLUTION = 8


# =============================================================================
# Netztyp
# =============================================================================

# "grid"  = regelmäßiges Rechteckgitter (schnell, einfach)
# "zonen" = Sichtbarkeitsgraph mit Knoten an Zonenecken (kompakter, präziser)
NETZ_TYP = "grid"


# =============================================================================
# Gitter-Netz-Einstellungen (nur bei NETZ_TYP = "grid")
# =============================================================================

# Abstand der Gitterknoten in Metern
NETZ_AUFLOESUNG_M = 250

# True = zusätzlich diagonale Nachbarknoten verbinden
DIAGONALE_VERBINDUNGEN = False

# Zusätzlicher Rand um die Bounding Box der Zonen in Metern (0 = kein Rand)
GRAPH_BBOX_PADDING_M = 0


# =============================================================================
# Zonen-Netz-Einstellungen (nur bei NETZ_TYP = "zonen")
# =============================================================================

# Abstand der Knoten von jeder Zonenecke in Metern
ZONEN_KNOTEN_ABSTAND_M = 10

# Maximale Kantenlänge in Metern (None = unbegrenzt)
ZONEN_MAX_KANTENLAENGE_M = 3000

# Kreuzungsfreier Graph?
#   True  = kürzeste, kreuzungsfreie Greedy-Auswahl (klassische Variante).
#   False = ALLE sichtbaren (zonen-freien) Kanten – dichter Graph mit
#           Kreuzungen, dafür deutlich schneller.
ZONEN_KREUZUNGSFREI = True


# =============================================================================
# Start- und Endpunkt
# =============================================================================

# Startpunkt in WGS84 (Breitengrad, Längengrad)
START_LAT = 48.14018493850112
START_LON = 11.56075451365665

# Endpunkt in WGS84
END_LAT = 48.07276903757395
END_LON = 11.637402988925738

# Anzahl der Verbindungen, die Start/End mit dem Graphen erhalten
# (nur beim Gitter-Netz; beim Zonen-Netz wird jeder sichtbare Knoten verbunden)
START_END_VERBINDUNGEN_PRO_PUNKT = 5


# =============================================================================
# Graphkarten (optional)
# =============================================================================

# Graphkarte ohne Sperrzonen
GRAPH_KARTE_ERSTELLEN  = False
GRAPH_KARTE_ANZEIGEN   = False

# Graphkarte mit Sperrzonen
GRAPH_KARTE_MIT_ZONEN_ERSTELLEN = False
GRAPH_KARTE_MIT_ZONEN_ANZEIGEN  = False


# =============================================================================
# Wegsuche
# =============================================================================

# True = Wegsuche durchführen und Ergebnis speichern
WEGSUCHE_AUSFUEHREN = True

# "dijkstra" oder "astar"
WEGSUCHE_ALGORITHMUS = "astar"

# True = Routenkarte nach Erzeugung anzeigen
WEGSUCHE_KARTE_ANZEIGEN = True


# =============================================================================
# Kartenhintergrund und Ausschnitt
# =============================================================================

# True = Satellitenbilder (Esri World Imagery) als Hintergrund laden
SATELLITE_BACKGROUND = True

# Zoom-Stufe für den Kachel-Hintergrund (höher = mehr Detail, langsamer)
BASEMAP_ZOOM = 13

# Mittelpunkt des festen PNG-Ausschnitts in WGS84
PNG_CENTER_LAT = 48.1085
PNG_CENTER_LON = 11.5953

# Seitenlänge des quadratischen Ausschnitts in Kilometern
PNG_SQUARE_SIDE_KM = 15

# True = fester Ausschnitt (oben), False = Ausschnitt aus Datengrenzen
PNG_FESTER_AUSSCHNITT = True


# =============================================================================
# Graph-Begrenzung auf den sichtbaren Bereich
# =============================================================================

# True = Graph wird auf den PNG-Ausschnitt begrenzt.
# Knoten außerhalb des sichtbaren Quadrats werden nicht erzeugt, sodass der
# Algorithmus keine Route außerhalb des Ausschnitts finden kann.
# Hat nur Wirkung wenn PNG_FESTER_AUSSCHNITT = True.
GRAPH_AUF_PNG_BEGRENZEN = True


# =============================================================================
# Hilfsfunktionen
# =============================================================================

def _ja_nein(value):
    """Gibt 'Ja' oder 'Nein' für boolesche Werte zurück."""
    return "Ja" if value else "Nein"


def _print_done(name, laufzeit_s):
    """Gibt den Abschluss eines Schritts mit Laufzeit auf der Konsole aus."""
    print(f"{name}: Done")
    print(f"Laufzeit: {laufzeit_s:.3f} s")
    print()


# =============================================================================
# Pipeline
# =============================================================================

def main():
    gesamte_laufzeit_start = perf_counter()

    base_dir = Path(__file__).parent

    # Alle Pfade relativ zum Skript-Verzeichnis aufbauen.
    input_geojson                = base_dir / INPUT_GEOJSON_NAME
    output_zones_geojson         = base_dir / OUTPUT_ZONES_GEOJSON_NAME
    output_graph_nodes           = base_dir / OUTPUT_GRAPH_NODES_NAME
    output_graph_edges           = base_dir / OUTPUT_GRAPH_EDGES_NAME
    output_graph_map_png         = base_dir / OUTPUT_GRAPH_MAP_PNG_NAME
    output_graph_map_with_zones  = base_dir / OUTPUT_GRAPH_MAP_WITH_ZONES_PNG_NAME
    output_route_nodes           = base_dir / OUTPUT_ROUTE_NODES_NAME
    output_route_edges           = base_dir / OUTPUT_ROUTE_EDGES_NAME
    output_route_map             = base_dir / OUTPUT_ROUTE_MAP_PNG_NAME

    output_format = OUTPUT_FORMAT.lower().strip()

    if output_format == "html":
        output_zones_map = base_dir / OUTPUT_ZONES_MAP_HTML_NAME
    elif output_format == "png":
        output_zones_map = base_dir / OUTPUT_ZONES_MAP_PNG_NAME
    else:
        raise ValueError("OUTPUT_FORMAT muss 'html' oder 'png' sein.")

    netz_typ = NETZ_TYP.lower().strip()

    if netz_typ not in {"grid", "zonen"}:
        raise ValueError("NETZ_TYP muss 'grid' oder 'zonen' sein.")

    # Bounding Box des PNG-Ausschnitts als harte Graph-Grenze berechnen.
    # Ist PNG_FESTER_AUSSCHNITT oder GRAPH_AUF_PNG_BEGRENZEN deaktiviert,
    # bleibt graph_bbox_wgs84 None und der Graph wird nicht beschnitten.
    if PNG_FESTER_AUSSCHNITT and GRAPH_AUF_PNG_BEGRENZEN:
        _half   = PNG_SQUARE_SIDE_KM / 2.0
        _dlat   = _half / 111.32
        _dlon   = _half / (111.32 * cos(radians(PNG_CENTER_LAT)))
        graph_bbox_wgs84 = (
            PNG_CENTER_LAT - _dlat,
            PNG_CENTER_LON - _dlon,
            PNG_CENTER_LAT + _dlat,
            PNG_CENTER_LON + _dlon,
        )
    else:
        graph_bbox_wgs84 = None

    # -------------------------------------------------------------------------
    # Schritt 1: GeoJSON bearbeiten – OSM-Daten klassifizieren und puffern
    # -------------------------------------------------------------------------

    start = perf_counter()

    zones = create_luftvo_buffer_geojson(
        input_geojson=input_geojson,
        output_geojson=output_zones_geojson,

        hospital_radius_m=100,
        police_radius_m=100,
        prison_radius_m=100,
        diplomatic_radius_m=100,
        government_radius_m=100,
        military_radius_m=100,
        industrial_radius_m=100,
        power_plant_radius_m=100,
        airport_radius_m=1000,
        aerodrome_radius_m=1500,
        airstrip_heliport_radius_m=1500,

        # Schutzgebiete bereits als Fläche – kein zusätzlicher Puffer.
        nature_protection_radius_m=0,
        landscape_protection_radius_m=0,

        polygon_corners=POLYGON_ECKEN_N,
        zone_buffer_resolution=ZONE_BUFFER_RESOLUTION,
    )

    _print_done("GeoJSON bearbeiten", perf_counter() - start)

    # -------------------------------------------------------------------------
    # Schritt 2: Graph erstellen – Navigationsnetz aufbauen
    # -------------------------------------------------------------------------

    start = perf_counter()

    if netz_typ == "grid":
        nodes, edges = create_grid_graph(
            zones_geojson=output_zones_geojson,
            output_nodes_geojson=output_graph_nodes,
            output_edges_geojson=output_graph_edges,

            spacing_m=NETZ_AUFLOESUNG_M,
            connect_diagonal=DIAGONALE_VERBINDUNGEN,
            bbox_padding_m=GRAPH_BBOX_PADDING_M,
            max_bbox_wgs84=graph_bbox_wgs84,

            start_lat=START_LAT,
            start_lon=START_LON,
            end_lat=END_LAT,
            end_lon=END_LON,
            special_connections_per_point=START_END_VERBINDUNGEN_PRO_PUNKT,
        )
        netz_text = "Grid"

    else:
        nodes, edges = create_zone_visibility_graph(
            zones_geojson=output_zones_geojson,
            output_nodes_geojson=output_graph_nodes,
            output_edges_geojson=output_graph_edges,

            node_offset_m=ZONEN_KNOTEN_ABSTAND_M,
            max_edge_distance_m=ZONEN_MAX_KANTENLAENGE_M,
            crossing_free=ZONEN_KREUZUNGSFREI,
            bbox_wgs84=graph_bbox_wgs84,

            start_lat=START_LAT,
            start_lon=START_LON,
            end_lat=END_LAT,
            end_lon=END_LON,
        )
        netz_text = "Zonen"

    _print_done("Graph erstellen", perf_counter() - start)

    # -------------------------------------------------------------------------
    # Schritt 3: Wegsuche – kürzesten Weg finden
    # -------------------------------------------------------------------------

    loesungs_laufzeit_s = None
    route_result        = None

    if WEGSUCHE_AUSFUEHREN:
        start = perf_counter()

        # Verbose-Ausgaben aus Wegfindungs.py unterdrücken – Zusammenfassung
        # erfolgt im Abschnitt "Abschlussinformationen" weiter unten.
        with redirect_stdout(io.StringIO()):
            route_result = find_path_and_visualize(
                nodes_geojson=output_graph_nodes,
                edges_geojson=output_graph_edges,
                zones_geojson=output_zones_geojson,
                output_route_nodes_geojson=output_route_nodes,
                output_route_edges_geojson=output_route_edges,
                output_png=output_route_map,
                algorithm=WEGSUCHE_ALGORITHMUS,
                directed=False,
                show_map=WEGSUCHE_KARTE_ANZEIGEN,
                satellite_background=SATELLITE_BACKGROUND,
                basemap_zoom=BASEMAP_ZOOM,
                fixed_extent=PNG_FESTER_AUSSCHNITT,
                center_lat=PNG_CENTER_LAT,
                center_lon=PNG_CENTER_LON,
                square_side_km=PNG_SQUARE_SIDE_KM,
            )

        loesungs_laufzeit_s = perf_counter() - start

    # -------------------------------------------------------------------------
    # Schritt 4a: LuftVO-Zonenkarte (optional)
    # -------------------------------------------------------------------------

    if ZONEN_KARTE_ERSTELLEN:
        create_geojson_visualization(
            input_geojson=output_zones_geojson,
            output_path=output_zones_map,
            output_format=output_format,
            open_in_browser=ZONEN_KARTE_ANZEIGEN,
            show_png=ZONEN_KARTE_ANZEIGEN,
            satellite_background=SATELLITE_BACKGROUND,
            basemap_zoom=BASEMAP_ZOOM,
            fixed_png_extent=PNG_FESTER_AUSSCHNITT,
            png_center_lat=PNG_CENTER_LAT,
            png_center_lon=PNG_CENTER_LON,
            png_square_side_km=PNG_SQUARE_SIDE_KM,
        )

    # -------------------------------------------------------------------------
    # Schritt 4b: Graphkarte ohne Zonen (optional)
    # -------------------------------------------------------------------------

    if GRAPH_KARTE_ERSTELLEN:
        create_graph_png_map(
            nodes_geojson=output_graph_nodes,
            edges_geojson=output_graph_edges,
            zones_geojson=None,
            output_png=output_graph_map_png,
            show_map=GRAPH_KARTE_ANZEIGEN,
            satellite_background=SATELLITE_BACKGROUND,
            basemap_zoom=BASEMAP_ZOOM,
            fixed_extent=PNG_FESTER_AUSSCHNITT,
            center_lat=PNG_CENTER_LAT,
            center_lon=PNG_CENTER_LON,
            square_side_km=PNG_SQUARE_SIDE_KM,
        )

    # -------------------------------------------------------------------------
    # Schritt 4c: Graphkarte mit Zonen (optional)
    # -------------------------------------------------------------------------

    if GRAPH_KARTE_MIT_ZONEN_ERSTELLEN:
        create_graph_png_map(
            nodes_geojson=output_graph_nodes,
            edges_geojson=output_graph_edges,
            zones_geojson=output_zones_geojson,
            output_png=output_graph_map_with_zones,
            show_map=GRAPH_KARTE_MIT_ZONEN_ANZEIGEN,
            satellite_background=SATELLITE_BACKGROUND,
            basemap_zoom=BASEMAP_ZOOM,
            fixed_extent=PNG_FESTER_AUSSCHNITT,
            center_lat=PNG_CENTER_LAT,
            center_lon=PNG_CENTER_LON,
            square_side_km=PNG_SQUARE_SIDE_KM,
        )

    # -------------------------------------------------------------------------
    # Abschlussinformationen
    # -------------------------------------------------------------------------

    gesamte_laufzeit_s = perf_counter() - gesamte_laufzeit_start

    if route_result is not None:
        algorithmus_text      = "AStar" if route_result["algorithm"] == "astar" else "Dijkstra"
        laenge_text           = (
            f"{route_result['route_length_m']:.2f} m / "
            f"{route_result['route_length_km']:.3f} km"
        )
        loesungs_laufzeit_text = f"{loesungs_laufzeit_s:.3f} s"
    else:
        algorithmus_text       = "-"
        laenge_text            = "-"
        loesungs_laufzeit_text = "-"

    print("=" * 77)
    print("ZUSAMMENFASSUNG")
    print("=" * 77)
    print(f"Netz generiert:               {netz_text}")
    print(f"Knoten erzeugt:               {len(nodes)}")
    print(f"Kanten erzeugt:               {len(edges)}")
    print()
    print("KARTEN:")
    print(f"  Satellitenhintergrund:        {_ja_nein(SATELLITE_BACKGROUND)}")
    print(f"  Zonenkarte:                   {_ja_nein(ZONEN_KARTE_ERSTELLEN)}")
    print(f"  Graphkarte:                   {_ja_nein(GRAPH_KARTE_ERSTELLEN)}")
    print(f"  Graphkarte mit Zonen:         {_ja_nein(GRAPH_KARTE_MIT_ZONEN_ERSTELLEN)}")
    print()
    print("LÖSUNG:")
    print(f"  Algorithmus:                  {algorithmus_text}")
    print(f"  Länge:                        {laenge_text}")
    print(f"  Laufzeit Wegsuche:            {loesungs_laufzeit_text}")
    print()
    print(f"Gesamte Laufzeit:             {gesamte_laufzeit_s:.3f} s")
    print("=" * 77)


if __name__ == "__main__":
    main()
