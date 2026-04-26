from pathlib import Path

from GeoJSON_Bearbeiten import create_luftvo_buffer_geojson
from geoJSON_viewer import create_geojson_visualization
from graph_erstellen import create_navigation_graph


# -------------------------------------------------
# Zentrale Einstellungen
# -------------------------------------------------

INPUT_GEOJSON_NAME = "GeoDaten_Overpass.geojson"

OUTPUT_ZONES_GEOJSON_NAME = "drohnen_luftvo_zonen.geojson"
OUTPUT_GRAPH_NODES_NAME = "graph_nodes.geojson"
OUTPUT_GRAPH_EDGES_NAME = "graph_edges.geojson"

# Hier festlegen:
# "html" oder "png"
OUTPUT_FORMAT = "png"

# Graph-Einstellungen
GRAPH_SPACING_M = 250
CONNECT_DIAGONAL = True

# PNG-Satellitenhintergrund
SATELLITE_BACKGROUND = True
BASEMAP_ZOOM = 13


def main():
    base_dir = Path(__file__).parent

    input_geojson = base_dir / INPUT_GEOJSON_NAME

    output_zones_geojson = base_dir / OUTPUT_ZONES_GEOJSON_NAME
    output_graph_nodes = base_dir / OUTPUT_GRAPH_NODES_NAME
    output_graph_edges = base_dir / OUTPUT_GRAPH_EDGES_NAME

    output_format = OUTPUT_FORMAT.lower().strip()

    if output_format == "html":
        output_visualization = base_dir / "drohnen_luftvo_karte.html"
    elif output_format == "png":
        output_visualization = base_dir / "drohnen_luftvo_karte.png"
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
        spacing_m=GRAPH_SPACING_M,
        connect_diagonal=CONNECT_DIAGONAL,
        bbox_padding_m=0,
    )

    print("Graph wurde erzeugt.")
    print(f"Knoten-Abstand: {GRAPH_SPACING_M} m")
    print(f"Diagonale Verbindungen: {CONNECT_DIAGONAL}")
    print(f"Knoten: {len(nodes)}")
    print(f"Kanten: {len(edges)}")
    print(f"Graph-Knoten-Datei: {output_graph_nodes}")
    print(f"Graph-Kanten-Datei: {output_graph_edges}")

    # -------------------------------------------------
    # 3. Karte visualisieren
    # -------------------------------------------------

    visualization_file = create_geojson_visualization(
        input_geojson=output_zones_geojson,
        output_path=output_visualization,
        output_format=output_format,

        # Nur für HTML relevant
        open_in_browser=True,

        # Nur für PNG relevant
        show_png=True,
        satellite_background=SATELLITE_BACKGROUND,
        basemap_zoom=BASEMAP_ZOOM,
    )

    print("Kartenausgabe wurde erzeugt.")
    print(f"Kartendatei: {visualization_file}")


if __name__ == "__main__":
    main()