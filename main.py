from pathlib import Path
from time import perf_counter
from contextlib import redirect_stdout
import io

from GeoJSON_Bearbeiten import create_luftvo_buffer_geojson
from geoJSON_viewer import create_geojson_visualization
from graph_erstellen import create_navigation_graph as create_grid_graph
from Graph_erstellen_Zonen import create_zone_visibility_graph
from graph_viewer import create_graph_png_map
from Wegfindungs import find_path_and_visualize


# -------------------------------------------------
# Zentrale Eingabe-/Ausgabedateien
# -------------------------------------------------

INPUT_GEOJSON_NAME = "GeoDaten_ohne_Naturschutz.geojson"

OUTPUT_ZONES_GEOJSON_NAME = "drohnen_luftvo_zonen.geojson"
OUTPUT_GRAPH_NODES_NAME = "graph_nodes.geojson"
OUTPUT_GRAPH_EDGES_NAME = "graph_edges.geojson"

OUTPUT_ZONES_MAP_HTML_NAME = "drohnen_luftvo_karte.html"
OUTPUT_ZONES_MAP_PNG_NAME = "drohnen_luftvo_karte.png"

OUTPUT_GRAPH_MAP_PNG_NAME = "graph_karte.png"
OUTPUT_GRAPH_MAP_WITH_ZONES_PNG_NAME = "graph_karte_mit_zonen.png"

OUTPUT_ROUTE_NODES_NAME = "route_nodes.geojson"
OUTPUT_ROUTE_EDGES_NAME = "route_edges.geojson"
OUTPUT_ROUTE_MAP_PNG_NAME = "route_karte.png"


# -------------------------------------------------
# LuftVO-Zonenkarte
# -------------------------------------------------

OUTPUT_FORMAT = "png"

ZONEN_KARTE_ERSTELLEN = False
ZONEN_KARTE_ANZEIGEN = False


# -------------------------------------------------
# Polygon-Einstellungen für GeoJSON_Bearbeiten.py
# -------------------------------------------------

POLYGON_ECKEN_N = 12
ZONE_BUFFER_RESOLUTION = 16


# -------------------------------------------------
# Welches Netz soll erzeugt werden?
# -------------------------------------------------

# "grid"  = regelmäßiges Punktnetz
# "zonen" = Knoten 10 m um die Ecken der Zonen
NETZ_TYP = "zonen"


# -------------------------------------------------
# Grid-Netz-Einstellungen
# -------------------------------------------------

NETZ_AUFLOESUNG_M = 250
DIAGONALE_VERBINDUNGEN = False
GRAPH_BBOX_PADDING_M = 0


# -------------------------------------------------
# Zonen-Netz-Einstellungen
# -------------------------------------------------

# Abstand der Knoten von jeder Zonenecke
ZONEN_KNOTEN_ABSTAND_M = 10

# True = Kanten dürfen keine anderen Kanten schneiden
ZONEN_KANTEN_KREUZUNGEN_VERMEIDEN = True

# None = alle sichtbaren Kanten prüfen
# Beispiel: 3000 = nur Kanten bis 3000 m Länge
ZONEN_MAX_KANTENLAENGE_M = 3000


# -------------------------------------------------
# Start- und Endpunkt
# -------------------------------------------------

START_LAT = 48.14018493850112
START_LON = 11.56075451365665

END_LAT = 48.07276903757395
END_LON = 11.637402988925738

START_END_VERBINDUNGEN_PRO_PUNKT = 5


# -------------------------------------------------
# Graph-Karten
# -------------------------------------------------

GRAPH_KARTE_ERSTELLEN = False
GRAPH_KARTE_ANZEIGEN = False

GRAPH_KARTE_MIT_ZONEN_ERSTELLEN = False
GRAPH_KARTE_MIT_ZONEN_ANZEIGEN = False


# -------------------------------------------------
# Wegsuche
# -------------------------------------------------

WEGSUCHE_AUSFUEHREN = True

# "dijkstra" oder "astar"
WEGSUCHE_ALGORITHMUS = "astar"

WEGSUCHE_KARTE_ANZEIGEN = True


# -------------------------------------------------
# PNG-/Satellitenhintergrund
# -------------------------------------------------

SATELLITE_BACKGROUND = True
BASEMAP_ZOOM = 13


# -------------------------------------------------
# Fester Kartenausschnitt für PNG
# -------------------------------------------------

PNG_CENTER_LAT = 48.137154
PNG_CENTER_LON = 11.576124

PNG_SQUARE_SIDE_KM = 25
PNG_FESTER_AUSSCHNITT = True


def ja_nein(value):
    return "Ja" if value else "Nein"


def print_done(name, laufzeit_s):
    print(f"{name}: Done")
    print(f"Laufzeit: {laufzeit_s:.3f} s")
    print()


def main():
    gesamte_laufzeit_start = perf_counter()

    base_dir = Path(__file__).parent

    input_geojson = base_dir / INPUT_GEOJSON_NAME

    output_zones_geojson = base_dir / OUTPUT_ZONES_GEOJSON_NAME
    output_graph_nodes = base_dir / OUTPUT_GRAPH_NODES_NAME
    output_graph_edges = base_dir / OUTPUT_GRAPH_EDGES_NAME

    output_graph_map_png = base_dir / OUTPUT_GRAPH_MAP_PNG_NAME
    output_graph_map_with_zones_png = base_dir / OUTPUT_GRAPH_MAP_WITH_ZONES_PNG_NAME

    output_route_nodes = base_dir / OUTPUT_ROUTE_NODES_NAME
    output_route_edges = base_dir / OUTPUT_ROUTE_EDGES_NAME
    output_route_map = base_dir / OUTPUT_ROUTE_MAP_PNG_NAME

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

    # -------------------------------------------------
    # 1. GeoJSON bearbeiten
    # -------------------------------------------------

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

        nature_protection_radius_m=0,
        landscape_protection_radius_m=0,

        polygon_corners=POLYGON_ECKEN_N,
        zone_buffer_resolution=ZONE_BUFFER_RESOLUTION,
    )

    geojson_laufzeit_s = perf_counter() - start
    print_done("geojson bearbeiten", geojson_laufzeit_s)

    # -------------------------------------------------
    # 2. Graph erstellen
    # -------------------------------------------------

    start = perf_counter()

    if netz_typ == "grid":
        nodes, edges = create_grid_graph(
            zones_geojson=output_zones_geojson,
            output_nodes_geojson=output_graph_nodes,
            output_edges_geojson=output_graph_edges,

            spacing_m=NETZ_AUFLOESUNG_M,
            connect_diagonal=DIAGONALE_VERBINDUNGEN,
            bbox_padding_m=GRAPH_BBOX_PADDING_M,

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
            prevent_edge_crossings=ZONEN_KANTEN_KREUZUNGEN_VERMEIDEN,
            max_edge_distance_m=ZONEN_MAX_KANTENLAENGE_M,

            start_lat=START_LAT,
            start_lon=START_LON,
            end_lat=END_LAT,
            end_lon=END_LON,
        )

        netz_text = "Zonen"

    graph_laufzeit_s = perf_counter() - start
    print_done("graph erstellen", graph_laufzeit_s)

    # -------------------------------------------------
    # 3. Wegsuche
    # -------------------------------------------------

    loesungs_laufzeit_s = None
    route_result = None

    if WEGSUCHE_AUSFUEHREN:
        start = perf_counter()

        # Ausführliche Prints aus Wegfindungs.py unterdrücken
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

    # -------------------------------------------------
    # 4. LuftVO-Zonenkarte
    # -------------------------------------------------

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

    # -------------------------------------------------
    # 5. Graph-Karte ohne Zonen
    # -------------------------------------------------

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

    # -------------------------------------------------
    # 6. Graph-Karte mit Zonen
    # -------------------------------------------------

    if GRAPH_KARTE_MIT_ZONEN_ERSTELLEN:
        create_graph_png_map(
            nodes_geojson=output_graph_nodes,
            edges_geojson=output_graph_edges,
            zones_geojson=output_zones_geojson,
            output_png=output_graph_map_with_zones_png,
            show_map=GRAPH_KARTE_MIT_ZONEN_ANZEIGEN,
            satellite_background=SATELLITE_BACKGROUND,
            basemap_zoom=BASEMAP_ZOOM,
            fixed_extent=PNG_FESTER_AUSSCHNITT,
            center_lat=PNG_CENTER_LAT,
            center_lon=PNG_CENTER_LON,
            square_side_km=PNG_SQUARE_SIDE_KM,
        )

    # -------------------------------------------------
    # 7. Abschlussinformationen
    # -------------------------------------------------

    gesamte_laufzeit_s = perf_counter() - gesamte_laufzeit_start

    if route_result is not None:
        algorithmus_text = "AStar" if route_result["algorithm"] == "astar" else "Dijkstra"
        laenge_text = (
            f"{route_result['route_length_m']:.2f} m / "
            f"{route_result['route_length_km']:.3f} km"
        )
        loesungs_laufzeit_text = f"{loesungs_laufzeit_s:.3f} s"
    else:
        algorithmus_text = "-"
        laenge_text = "-"
        loesungs_laufzeit_text = "-"

    print("-----------------------------------------------------------------------------")
    print("Informationen")
    print("-----------------------------------------------------------------------------")
    print(f"Netz generiert: {netz_text}")
    print(f"Knoten erzeugt: {len(nodes)}")
    print(f"Kanten erzeugt: {len(edges)}")
    print()
    print("KARTEN:")
    print(f"Hintergrund Karte: {ja_nein(SATELLITE_BACKGROUND)}")
    print(f"Zonen Karte: {ja_nein(ZONEN_KARTE_ERSTELLEN)}")
    print(f"Hintergrundkarte mit Zonen: {ja_nein(ZONEN_KARTE_ERSTELLEN and SATELLITE_BACKGROUND)}")
    print(f"Graph Karte: {ja_nein(GRAPH_KARTE_ERSTELLEN)}")
    print(f"Hintergrundkarte mit Zonen und Graph: {ja_nein(GRAPH_KARTE_MIT_ZONEN_ERSTELLEN and SATELLITE_BACKGROUND)}")
    print()
    print("LÖSUNG:")
    print(f"Algorithmus: {algorithmus_text}")
    print(f"Länge: {laenge_text}")
    print(f"Laufzeit der Lösungsfindung: {loesungs_laufzeit_text}")
    print()
    print(f"Gesamte Laufzeit: {gesamte_laufzeit_s:.3f} s")
    print("-----------------------------------------------------------------------------")


if __name__ == "__main__":
    main()