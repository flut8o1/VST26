"""
main.py – Drohnen-Routenplanung (LuftVO-konform).

Pipeline: GeoJSON puffern → Gittergraph → Wegsuche → Routenkarte.
"""

from pathlib import Path
from time import perf_counter
from contextlib import redirect_stdout
from math import cos, radians
import io

from GeoJSON_Bearbeiten import create_luftvo_buffer_geojson
from graph_erstellen import create_navigation_graph as create_grid_graph
from Wegfindungs import find_path_and_visualize


# =============================================================================
# Eingabe / Ausgabe
# =============================================================================

INPUT_GEOJSON_NAME    = "GeoDaten_ohne_Naturschutz.geojson"
OUTPUT_ROUTE_MAP_NAME = "route_karte.png"


# =============================================================================
# Puffer-Einstellungen
# =============================================================================

POLYGON_ECKEN_N        = 8
ZONE_BUFFER_RESOLUTION = 8


# =============================================================================
# Gitter-Netz-Einstellungen
# =============================================================================

NETZ_AUFLOESUNG_M              = 250
DIAGONALE_VERBINDUNGEN         = False
GRAPH_BBOX_PADDING_M           = 0


# =============================================================================
# Start- und Endpunkt
# =============================================================================

START_LAT = 48.14018493850112
START_LON = 11.56075451365665

END_LAT = 48.07276903757395
END_LON = 11.637402988925738

START_END_VERBINDUNGEN_PRO_PUNKT = 5


# =============================================================================
# Wegsuche
# =============================================================================

WEGSUCHE_AUSFUEHREN     = True
WEGSUCHE_ALGORITHMUS    = "astar"
WEGSUCHE_KARTE_ANZEIGEN = True


# =============================================================================
# Kartenhintergrund und Ausschnitt
# =============================================================================

SATELLITE_BACKGROUND  = True
BASEMAP_ZOOM          = 13

PNG_CENTER_LAT        = 48.1085
PNG_CENTER_LON        = 11.5953
PNG_SQUARE_SIDE_KM    = 15
PNG_FESTER_AUSSCHNITT = True


# =============================================================================
# Graph-Begrenzung auf den sichtbaren Bereich
# =============================================================================

# True = Graph wird auf den PNG-Ausschnitt begrenzt.
# Hat nur Wirkung wenn PNG_FESTER_AUSSCHNITT = True.
GRAPH_AUF_PNG_BEGRENZEN = True


# =============================================================================
# Hilfsfunktionen
# =============================================================================

def _ja_nein(value):
    return "Ja" if value else "Nein"


def _print_done(name, laufzeit_s):
    print(f"{name}: Done")
    print(f"Laufzeit: {laufzeit_s:.3f} s")
    print()


# =============================================================================
# Pipeline
# =============================================================================

def main():
    gesamte_laufzeit_start = perf_counter()

    base_dir = Path(__file__).parent

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
    # Schritt 1: GeoJSON bearbeiten
    # -------------------------------------------------------------------------

    start = perf_counter()

    create_luftvo_buffer_geojson(
        input_geojson=base_dir / INPUT_GEOJSON_NAME,
        output_geojson=base_dir / "drohnen_luftvo_zonen.geojson",
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
        nature_protection_radius_m=0,
        landscape_protection_radius_m=0,
        polygon_corners=POLYGON_ECKEN_N,
        zone_buffer_resolution=ZONE_BUFFER_RESOLUTION,
    )

    _print_done("GeoJSON bearbeiten", perf_counter() - start)

    # -------------------------------------------------------------------------
    # Schritt 2: Graph erstellen
    # -------------------------------------------------------------------------

    start = perf_counter()

    nodes, edges = create_grid_graph(
        zones_geojson=base_dir / "drohnen_luftvo_zonen.geojson",
        output_nodes_geojson=base_dir / "graph_nodes.geojson",
        output_edges_geojson=base_dir / "graph_edges.geojson",
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

    _print_done("Graph erstellen", perf_counter() - start)

    # -------------------------------------------------------------------------
    # Schritt 3: Wegsuche
    # -------------------------------------------------------------------------

    loesungs_laufzeit_s = None
    route_result        = None

    if WEGSUCHE_AUSFUEHREN:
        start = perf_counter()

        with redirect_stdout(io.StringIO()):
            route_result = find_path_and_visualize(
                nodes_geojson=base_dir / "graph_nodes.geojson",
                edges_geojson=base_dir / "graph_edges.geojson",
                zones_geojson=base_dir / "drohnen_luftvo_zonen.geojson",
                output_route_nodes_geojson=base_dir / "route_nodes.geojson",
                output_route_edges_geojson=base_dir / "route_edges.geojson",
                output_png=base_dir / OUTPUT_ROUTE_MAP_NAME,
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
    # Abschlussinformationen
    # -------------------------------------------------------------------------

    gesamte_laufzeit_s = perf_counter() - gesamte_laufzeit_start

    if route_result is not None:
        algorithmus_text       = "AStar" if route_result["algorithm"] == "astar" else "Dijkstra"
        laenge_text            = f"{route_result['route_length_m']:.2f} m / {route_result['route_length_km']:.3f} km"
        loesungs_laufzeit_text = f"{loesungs_laufzeit_s:.3f} s"
    else:
        algorithmus_text       = "-"
        laenge_text            = "-"
        loesungs_laufzeit_text = "-"

    print("=" * 77)
    print("ZUSAMMENFASSUNG")
    print("=" * 77)
    print(f"Knoten erzeugt:               {len(nodes)}")
    print(f"Kanten erzeugt:               {len(edges)}")
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
