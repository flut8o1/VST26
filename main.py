from pathlib import Path

from GeoJSON_Bearbeiten import create_luftvo_buffer_geojson
from geoJSON_viewer import create_geojson_visualization
from graph_erstellen import create_navigation_graph
from graph_viewer import create_graph_png_map
from Wegfindungs import find_path_and_visualize


# -------------------------------------------------
# Zentrale Eingabe-/Ausgabedateien
# -------------------------------------------------

INPUT_GEOJSON_NAME = "GeoDaten_mit_Naturschutz.geojson"

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

# "html" oder "png"
OUTPUT_FORMAT = "png"

# Soll die LuftVO-Zonenkarte überhaupt erzeugt werden?
ZONEN_KARTE_ERSTELLEN = True

# Soll die LuftVO-Zonenkarte direkt angezeigt/geöffnet werden?
ZONEN_KARTE_ANZEIGEN = True


# -------------------------------------------------
# Graph-/Netz-Einstellungen
# -------------------------------------------------

# Abstand zwischen zwei Gitterknoten in Metern
NETZ_AUFLOESUNG_M = 100

# True  = zusätzlich diagonale direkte Nachbarn verbinden
# False = nur Nord-Süd / West-Ost, also horizontale und vertikale direkte Nachbarn
DIAGONALE_VERBINDUNGEN = False

# Erweiterung der Graph-Bounding-Box in Metern
GRAPH_BBOX_PADDING_M = 0


# -------------------------------------------------
# Start- und Endpunkt
# -------------------------------------------------

START_LAT = 48.14018493850112
START_LON = 11.56075451365665

END_LAT = 48.07276903757395
END_LON = 11.637402988925738

# Wie viele sichtbare nächste Gitterpunkte mit Start/Ende verbunden werden sollen.
# 1 = nur der nächste erlaubte Punkt
START_END_VERBINDUNGEN_PRO_PUNKT = 5


# -------------------------------------------------
# Graph-Karten
# -------------------------------------------------

# Graph ohne Zonen im Hintergrund
GRAPH_KARTE_ERSTELLEN = False
GRAPH_KARTE_ANZEIGEN = False

# Graph mit Zonen im Hintergrund
GRAPH_KARTE_MIT_ZONEN_ERSTELLEN = False
GRAPH_KARTE_MIT_ZONEN_ANZEIGEN = False


# -------------------------------------------------
# Wegsuche
# -------------------------------------------------

# Soll die Wegsuche ausgeführt werden?
WEGSUCHE_AUSFUEHREN = True

# "dijkstra" oder "astar"
WEGSUCHE_ALGORITHMUS = "astar"

# Soll die Wegsuche-Karte direkt angezeigt werden?
WEGSUCHE_KARTE_ANZEIGEN = True


# -------------------------------------------------
# PNG-/Satellitenhintergrund
# -------------------------------------------------

SATELLITE_BACKGROUND = True
BASEMAP_ZOOM = 13


# -------------------------------------------------
# Fester Kartenausschnitt für PNG
# -------------------------------------------------

# München Stadtmitte = Marienplatz
PNG_CENTER_LAT = 48.137154
PNG_CENTER_LON = 11.576124

# Seitenlänge des angezeigten Quadrats in Kilometern
PNG_SQUARE_SIDE_KM = 25

# True = PNG zeigt nur dieses Quadrat
PNG_FESTER_AUSSCHNITT = True


def main():
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

    # -------------------------------------------------
    # 1. LuftVO-Zonen erzeugen
    # -------------------------------------------------

    zones = create_luftvo_buffer_geojson(
        input_geojson=input_geojson,
        output_geojson=output_zones_geojson,

        # Einrichtungen / Behörden
        hospital_radius_m=100,
        police_radius_m=100,
        prison_radius_m=100,
        diplomatic_radius_m=100,
        government_radius_m=100,

        # Militär
        military_radius_m=100,

        # Industrie / Energie
        industrial_radius_m=100,
        power_plant_radius_m=100,

        # Luftverkehr
        airport_radius_m=1000,
        aerodrome_radius_m=1500,
        airstrip_heliport_radius_m=1500,

        # Naturschutz / Landschaftsschutz
        # 0 bedeutet: Gebiet selbst verwenden, nicht zusätzlich puffern
        nature_protection_radius_m=0,
        landscape_protection_radius_m=0,

        # Geometrie-Einstellungen
        polygon_corners=64,
        zone_buffer_resolution=16,
    )

    print("LuftVO-Zonen wurden erzeugt.")
    print(f"Eingabedatei: {input_geojson}")
    print(f"Zonen-GeoJSON: {output_zones_geojson}")
    print(f"Anzahl Zonen/Geometrien: {len(zones)}")

    # -------------------------------------------------
    # 2. Graph erzeugen
    # -------------------------------------------------

    nodes, edges = create_navigation_graph(
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

    print("Graph wurde erzeugt.")
    print(f"Netz-Auflösung: {NETZ_AUFLOESUNG_M} m")
    print(f"Diagonale Verbindungen im Gitter: {DIAGONALE_VERBINDUNGEN}")
    print(f"Startpunkt: {START_LAT}, {START_LON}")
    print(f"Endpunkt: {END_LAT}, {END_LON}")
    print(f"Knoten: {len(nodes)}")
    print(f"Kanten: {len(edges)}")
    print(f"Graph-Knoten-Datei: {output_graph_nodes}")
    print(f"Graph-Kanten-Datei: {output_graph_edges}")

    # -------------------------------------------------
    # 3. Wegsuche ausführen
    # -------------------------------------------------

    if WEGSUCHE_AUSFUEHREN:
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

        print("Wegsuche wurde abgeschlossen.")
        print(f"Algorithmus: {route_result['algorithm']}")
        print(f"Weglänge: {route_result['route_length_m']:.2f} m")
        print(f"Weglänge: {route_result['route_length_km']:.3f} km")
        print(f"Gesamtlänge aller Graph-Kanten: {route_result['total_graph_length_km']:.3f} km")
        print(f"Route-Nodes-Datei: {route_result['route_nodes_geojson']}")
        print(f"Route-Edges-Datei: {route_result['route_edges_geojson']}")
        print(f"Route-Karte: {route_result['route_map_png']}")
    else:
        print("Wegsuche wurde nicht ausgeführt.")

    # -------------------------------------------------
    # 4. LuftVO-Zonenkarte visualisieren
    # -------------------------------------------------

    if ZONEN_KARTE_ERSTELLEN:
        zones_map_file = create_geojson_visualization(
            input_geojson=output_zones_geojson,
            output_path=output_zones_map,
            output_format=output_format,

            # Nur für HTML relevant
            open_in_browser=ZONEN_KARTE_ANZEIGEN,

            # Nur für PNG relevant
            show_png=ZONEN_KARTE_ANZEIGEN,
            satellite_background=SATELLITE_BACKGROUND,
            basemap_zoom=BASEMAP_ZOOM,

            # Fester PNG-Ausschnitt
            fixed_png_extent=PNG_FESTER_AUSSCHNITT,
            png_center_lat=PNG_CENTER_LAT,
            png_center_lon=PNG_CENTER_LON,
            png_square_side_km=PNG_SQUARE_SIDE_KM,
        )

        print("LuftVO-Zonenkarte wurde erzeugt.")
        print(f"Zonen-Kartendatei: {zones_map_file}")
    else:
        print("LuftVO-Zonenkarte wurde nicht erzeugt.")

    # -------------------------------------------------
    # 5. Graph-Karte ohne Zonen visualisieren
    # -------------------------------------------------

    if GRAPH_KARTE_ERSTELLEN:
        graph_map_file = create_graph_png_map(
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

        print("Graph-Karte ohne Zonen wurde erzeugt.")
        print(f"Graph-Kartendatei: {graph_map_file}")
    else:
        print("Graph-Karte ohne Zonen wurde nicht erzeugt.")

    # -------------------------------------------------
    # 6. Graph-Karte mit Zonen visualisieren
    # -------------------------------------------------

    if GRAPH_KARTE_MIT_ZONEN_ERSTELLEN:
        graph_map_with_zones_file = create_graph_png_map(
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

        print("Graph-Karte mit Zonen wurde erzeugt.")
        print(f"Graph-mit-Zonen-Kartendatei: {graph_map_with_zones_file}")
    else:
        print("Graph-Karte mit Zonen wurde nicht erzeugt.")


if __name__ == "__main__":
    main()