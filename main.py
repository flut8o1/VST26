"""
main.py – Einstiegspunkt der Drohnen-Routenplanung (LuftVO-konform).

Gesamte Pipeline in drei Schritten:
    1. GeoJSON bearbeiten  – OSM-Daten klassifizieren und LuftVO-Zonen puffern.
    2. Graph erstellen     – Navigationsnetz (Gitter) aufbauen.
    3. Wegsuche            – Kürzesten Weg mit Dijkstra oder A* finden.

Die Schritte reichen ihre Ergebnisse als GeoDataFrames im Speicher weiter.
Einzige erzeugte Datei ist die Routenkarte (route_karte.png) – es werden
keine Zwischen-GeoJSON geschrieben.

Alle Einstellungen befinden sich ausschließlich in den Konfigurations-
abschnitten direkt unterhalb dieser Modulbeschreibung.
"""

from pathlib import Path
from time import perf_counter
from math import cos, radians

from Map_Preprocessing import create_luftvo_buffer_geojson
from Graph import create_navigation_graph as create_grid_graph
from Algorithmus import find_path_and_visualize


# =============================================================================
# Eingabe / Ausgabe
# =============================================================================

INPUT_GEOJSON_NAME    = "export.geojson"
OUTPUT_ROUTE_MAP_NAME = "route_karte.png"


# =============================================================================
# Puffer-Einstellungen (Map_Preprocessing.py)
# =============================================================================

# Anzahl der Ecken für Punkt-Polygone (höher = runder)
POLYGON_ECKEN_N = 8

# Rundheit der Puffer um bestehende Flächen/Linien (höher = runder)
ZONE_BUFFER_RESOLUTION = 8


# =============================================================================
# Gitter-Netz-Einstellungen
# =============================================================================

# Abstand der Gitterknoten in Metern (kleiner = feiner, aber mehr Knoten/Kanten)
NETZ_AUFLOESUNG_M = 50

# Zusätzlicher Rand um die Bounding Box der Zonen in Metern (0 = kein Rand)
GRAPH_BBOX_PADDING_M = 0


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
START_END_VERBINDUNGEN_PRO_PUNKT = 5


# =============================================================================
# Wegsuche
# =============================================================================

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

    # Bounding Box des PNG-Ausschnitts als harte Graph-Grenze berechnen.
    _half   = PNG_SQUARE_SIDE_KM / 2.0
    _dlat   = _half / 111.32
    _dlon   = _half / (111.32 * cos(radians(PNG_CENTER_LAT)))
    graph_bbox_wgs84 = (
        PNG_CENTER_LAT - _dlat,
        PNG_CENTER_LON - _dlon,
        PNG_CENTER_LAT + _dlat,
        PNG_CENTER_LON + _dlon,
    )

    # -------------------------------------------------------------------------
    # Schritt 1: GeoJSON bearbeiten – OSM-Daten klassifizieren und puffern
    # -------------------------------------------------------------------------

    start = perf_counter()

    zones = create_luftvo_buffer_geojson(
        input_geojson=base_dir / INPUT_GEOJSON_NAME,

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

    grid = create_grid_graph(
        zones=zones,

        spacing_m=NETZ_AUFLOESUNG_M,
        bbox_padding_m=GRAPH_BBOX_PADDING_M,
        max_bbox_wgs84=graph_bbox_wgs84,

        start_lat=START_LAT,
        start_lon=START_LON,
        end_lat=END_LAT,
        end_lon=END_LON,
        special_connections_per_point=START_END_VERBINDUNGEN_PRO_PUNKT,
    )

    _print_done("Graph erstellen", perf_counter() - start)

    # -------------------------------------------------------------------------
    # Schritt 3: Wegsuche – kürzesten Weg finden
    # -------------------------------------------------------------------------

    start = perf_counter()

    route_result = find_path_and_visualize(
        grid,
        zones=zones,
        output_png=base_dir / OUTPUT_ROUTE_MAP_NAME,
        algorithm=WEGSUCHE_ALGORITHMUS,
        show_map=WEGSUCHE_KARTE_ANZEIGEN,
        satellite_background=SATELLITE_BACKGROUND,
        basemap_zoom=BASEMAP_ZOOM,
        fixed_extent=True,
        center_lat=PNG_CENTER_LAT,
        center_lon=PNG_CENTER_LON,
        square_side_km=PNG_SQUARE_SIDE_KM,
    )

    loesungs_laufzeit_s = perf_counter() - start

    # -------------------------------------------------------------------------
    # Abschlussinformationen
    # -------------------------------------------------------------------------

    gesamte_laufzeit_s = perf_counter() - gesamte_laufzeit_start

    algorithmus_text       = "AStar" if route_result["algorithm"] == "astar" else "Dijkstra"
    laenge_text            = (
        f"{route_result['route_length_m']:.2f} m / "
        f"{route_result['route_length_km']:.3f} km"
    )
    loesungs_laufzeit_text = f"{loesungs_laufzeit_s:.3f} s"

    print("=" * 77)
    print("ZUSAMMENFASSUNG")
    print("=" * 77)
    print(f"Knoten erzeugt:               {len(grid.nodes_gdf)}")
    print(f"Kanten erzeugt:               {len(grid.edges_gdf)}")
    print()
    print("KARTEN:")
    print(f"  Satellitenhintergrund:        {_ja_nein(SATELLITE_BACKGROUND)}")
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
