from GeoJSON_Bearbeiten import create_luftvo_buffer_geojson
from geoJSON_viewer import create_geojson_map


def main():
    input_file = "overpass_export.geojson"
    output_geojson = "drohnen_luftvo_kreise.geojson"
    output_html = "drohnen_luftvo_karte.html"

    result = create_luftvo_buffer_geojson(
        input_geojson=input_file,
        output_geojson=output_geojson,
        points_only=True,
    )

    print("GeoJSON wurde erzeugt.")
    print(f"Anzahl Kreise: {len(result)}")

    map_file = create_geojson_map(
        input_geojson=output_geojson,
        output_html=output_html,
        open_in_browser=True,
    )

    print(f"Karte wurde erzeugt: {map_file}")


if __name__ == "__main__":
    main()