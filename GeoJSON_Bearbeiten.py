from pathlib import Path

import geopandas as gpd


WGS84 = "EPSG:4326"
DEFAULT_METRIC_CRS = "EPSG:25832"  # Für München sinnvoll: UTM Zone 32N


def _norm(value):
    """Kleinschreibung + String-Konvertierung für Tag-Vergleiche."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _get_tag(props, key):
    """
    Holt OSM-Tags robust.
    Funktioniert mit flachen GeoJSON-Properties und mit verschachteltem tags-Objekt.
    """
    if key in props:
        return props.get(key)

    tags = props.get("tags")
    if isinstance(tags, dict):
        return tags.get(key)

    return None


def classify_luftvo_radius(props):
    """
    Bestimmt anhand der OSM-Tags den LuftVO-Radius.

    Rückgabe:
        tuple[int | None, str | None]
        Beispiel: (100, "hospital_100m")
    """

    amenity = _norm(_get_tag(props, "amenity"))
    healthcare = _norm(_get_tag(props, "healthcare"))
    office = _norm(_get_tag(props, "office"))
    aeroway = _norm(_get_tag(props, "aeroway"))
    landuse = _norm(_get_tag(props, "landuse"))
    boundary = _norm(_get_tag(props, "boundary"))
    military = _norm(_get_tag(props, "military"))

    aerodrome_type = _norm(_get_tag(props, "aerodrome:type"))
    aerodrome = _norm(_get_tag(props, "aerodrome"))
    iata = _norm(_get_tag(props, "iata"))
    icao = _norm(_get_tag(props, "icao"))

    # Krankenhäuser: 100 m
    if amenity == "hospital" or healthcare == "hospital":
        return 100, "hospital_100m"

    # Behörden / Verwaltung / Polizei: 100 m
    if office == "government" or amenity in {
        "townhall",
        "courthouse",
        "police",
    }:
        return 100, "government_or_security_100m"

    # Militärische Anlagen: 100 m
    if landuse == "military" or boundary == "military" or military:
        return 100, "military_100m"

    # Flughäfen: 1.000 m als Kreis-Näherung
    airport_indicators = {
        "airport",
        "international",
        "regional",
        "commercial",
        "major",
        "large_airport",
        "medium_airport",
    }

    if aeroway == "aerodrome":
        if aerodrome_type in airport_indicators or aerodrome in airport_indicators or iata or icao:
            return 1000, "airport_1000m_circle_only_no_runway_corridor"

        # Flugplatz, der nicht als Flughafen erkannt wurde
        return 1500, "aerodrome_not_airport_1500m"

    # Airstrips / Heliports / Helipads: Näherung
    if aeroway in {"airstrip", "heliport", "helipad"}:
        return 1500, "airstrip_or_heliport_1500m_approx"

    return None, None


def create_luftvo_buffer_geojson(
    input_geojson,
    output_geojson,
    *,
    points_only=True,
    metric_crs=DEFAULT_METRIC_CRS,
    buffer_resolution=64,
):
    """
    Liest eine GeoJSON-Datei ein und erzeugt eine neue GeoJSON-Datei
    mit LuftVO-Pufferkreisen um passende Punkt-Features.

    Parameter:
        input_geojson:
            Pfad zur Eingabe-GeoJSON-Datei.

        output_geojson:
            Pfad zur Ausgabe-GeoJSON-Datei.

        points_only:
            True = nur Point/MultiPoint-Geometrien puffern.
            False = auch Linien/Flächen puffern.

        metric_crs:
            Koordinatensystem mit Meter-Einheit.
            Für München ist EPSG:25832 geeignet.

        buffer_resolution:
            Je höher, desto runder werden die Kreise.

    Rückgabe:
        GeoDataFrame mit den erzeugten Pufferflächen.
    """

    input_path = Path(input_geojson)
    output_path = Path(output_geojson)

    if not input_path.exists():
        raise FileNotFoundError(f"Eingabedatei nicht gefunden: {input_path}")

    gdf = gpd.read_file(input_path)

    if gdf.empty:
        raise ValueError("Die Eingabe-GeoJSON enthält keine Features.")

    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    rows = []

    for _, row in gdf.iterrows():
        geom = row.geometry

        if geom is None or geom.is_empty:
            continue

        if points_only and geom.geom_type not in {"Point", "MultiPoint"}:
            continue

        props = row.drop(labels=["geometry"]).to_dict()

        radius_m, rule = classify_luftvo_radius(props)

        if radius_m is None:
            continue

        rows.append({
            **props,
            "luftvo_radius_m": radius_m,
            "luftvo_rule": rule,
            "geometry": geom,
        })

    if not rows:
        raise ValueError("Keine passenden Features für LuftVO-Puffer gefunden.")

    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84)

    # In metrisches CRS umwandeln, damit buffer() Meter verwendet
    out_metric = out.to_crs(metric_crs)

    out_metric["geometry"] = [
        geom.buffer(radius, resolution=buffer_resolution)
        for geom, radius in zip(out_metric.geometry, out_metric["luftvo_radius_m"])
    ]

    # Zurück zu WGS84 für GeoJSON
    out_wgs84 = out_metric.to_crs(WGS84)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_wgs84.to_file(output_path, driver="GeoJSON")

    return out_wgs84


def main():
    """
    Wird nur ausgeführt, wenn diese Datei direkt gestartet wird.
    Beim Import aus einem anderen Skript wird main() nicht automatisch ausgeführt.
    """

    input_file = "GeoDaten_Overpass.geojson.geojson"
    output_file = "drohnen_luftvo_kreise.geojson"

    result = create_luftvo_buffer_geojson(
        input_geojson=input_file,
        output_geojson=output_file,
        points_only=True,
    )

    print(f"Fertig: {output_file}")
    print(f"Anzahl erzeugter Kreise: {len(result)}")


if __name__ == "__main__":
    main()