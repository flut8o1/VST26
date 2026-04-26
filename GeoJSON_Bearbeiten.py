from pathlib import Path
from math import cos, sin, pi

import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon


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


def classify_luftvo_type(props):
    """
    Bestimmt anhand der OSM-Tags die Art des Objekts.

    Rückgabe:
        str | None
    """

    amenity = _norm(_get_tag(props, "amenity"))
    healthcare = _norm(_get_tag(props, "healthcare"))
    office = _norm(_get_tag(props, "office"))
    aeroway = _norm(_get_tag(props, "aeroway"))
    landuse = _norm(_get_tag(props, "landuse"))
    boundary = _norm(_get_tag(props, "boundary"))
    military = _norm(_get_tag(props, "military"))
    industrial = _norm(_get_tag(props, "industrial"))
    man_made = _norm(_get_tag(props, "man_made"))
    power = _norm(_get_tag(props, "power"))

    protect_class = _norm(_get_tag(props, "protect_class"))
    designation = _norm(_get_tag(props, "designation"))
    short_protection_title = _norm(_get_tag(props, "short_protection_title"))
    name = _norm(_get_tag(props, "name"))

    aerodrome_type = _norm(_get_tag(props, "aerodrome:type"))
    aerodrome = _norm(_get_tag(props, "aerodrome"))
    iata = _norm(_get_tag(props, "iata"))
    icao = _norm(_get_tag(props, "icao"))

    # Krankenhäuser
    if amenity == "hospital" or healthcare == "hospital":
        return "hospital"

    # Polizei
    if amenity == "police":
        return "police"

    # Justizvollzug / Gefängnisse
    if amenity == "prison":
        return "prison"

    # Botschaften / Konsulate / diplomatische Einrichtungen
    if office in {"diplomatic", "embassy", "consulate"}:
        return "diplomatic"

    if amenity in {"embassy", "consulate"}:
        return "diplomatic"

    # Behörden / Verwaltung
    # Achtung: office=government, townhall, courthouse ist rechtlich grob.
    if office == "government" or amenity in {"townhall", "courthouse"}:
        return "government_or_security"

    # Militär
    if landuse == "military" or boundary == "military" or military:
        return "military"

    # Industrie
    if landuse == "industrial" or industrial or man_made == "works":
        return "industrial"

    # Energieerzeugung
    if power == "plant":
        return "power_plant"

    # Naturschutzgebiete / Nationalparks / FFH / Vogelschutz
    if (
        boundary == "national_park"
        or protect_class in {"2", "4"}
        or "naturschutzgebiet" in designation
        or "nationalpark" in designation
        or "ffh" in designation
        or "vogelschutz" in designation
        or "special protection area" in designation
        or "site of community importance" in designation
    ):
        return "nature_protection"

    # Landschaftsschutzgebiete, z. B. Isarauen / Isarlandschaft
    if (
        protect_class == "5"
        or short_protection_title == "lsg"
        or "landschaftsschutzgebiet" in designation
        or "isarauen" in name
        or "isarlandschaft" in name
    ):
        return "landscape_protection"

    # Flughäfen / Flugplätze
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
        if (
            aerodrome_type in airport_indicators
            or aerodrome in airport_indicators
            or iata
            or icao
        ):
            return "airport"

        return "aerodrome"

    if aeroway in {"airstrip", "heliport", "helipad"}:
        return "airstrip_or_heliport"

    return None


def create_regular_polygon(point, radius_m, corners):
    """
    Erstellt ein regelmäßiges Polygon um einen Punkt.

    point:
        Shapely Point in metrischem CRS

    radius_m:
        Radius in Metern

    corners:
        Anzahl der Ecken des Polygons
    """

    if corners < 3:
        raise ValueError("polygon_corners muss mindestens 3 sein.")

    x = point.x
    y = point.y

    coordinates = []

    for i in range(corners):
        angle = 2 * pi * i / corners
        px = x + radius_m * cos(angle)
        py = y + radius_m * sin(angle)
        coordinates.append((px, py))

    coordinates.append(coordinates[0])

    return Polygon(coordinates)


def create_point_polygon_geometry(geom, radius_m, corners):
    """
    Erstellt Polygon oder MultiPolygon für Point oder MultiPoint.
    """

    if geom.geom_type == "Point":
        return create_regular_polygon(geom, radius_m, corners)

    if geom.geom_type == "MultiPoint":
        polygons = [
            create_regular_polygon(point, radius_m, corners)
            for point in geom.geoms
        ]
        return MultiPolygon(polygons)

    raise ValueError(f"Nicht unterstützte Punkt-Geometrie: {geom.geom_type}")


def create_buffered_zone_geometry(geom, radius_m, buffer_resolution):
    """
    Vergrößert eine Zone/Linie/Fläche um radius_m Meter.

    Bei radius_m = 0 wird die ursprüngliche Geometrie unverändert übernommen.
    """

    if radius_m == 0:
        return geom

    return geom.buffer(radius_m, resolution=buffer_resolution)


def create_luftvo_buffer_geojson(
    input_geojson,
    output_geojson,
    *,
    # Radien aus der Tabelle
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

    # Naturschutz / Landschaftsschutz
    # Diese Gebiete werden nicht seitlich gepuffert,
    # sondern als Gebiet selbst übernommen.
    nature_protection_radius_m=0,
    landscape_protection_radius_m=0,

    # Anzahl der Ecken für Punkt-Polygone
    polygon_corners=64,

    # Rundheit bei Puffern um Flächen/Linien
    zone_buffer_resolution=16,

    metric_crs=DEFAULT_METRIC_CRS,
):
    """
    Liest eine GeoJSON-Datei ein und erzeugt eine neue GeoJSON-Datei.

    Verhalten:
        - Point / MultiPoint:
            Es wird ein regelmäßiges Polygon mit polygon_corners Ecken
            und dem passenden Radius erzeugt.

        - Polygon / MultiPolygon / LineString / MultiLineString:
            Die bestehende Zone wird um den passenden Radius vergrößert.

        - Radius 0:
            Die Zone wird unverändert übernommen.
            Das ist z. B. für Naturschutzflächen sinnvoll.
    """

    radius_by_type = {
        "hospital": hospital_radius_m,
        "police": police_radius_m,
        "prison": prison_radius_m,
        "diplomatic": diplomatic_radius_m,
        "government_or_security": government_radius_m,
        "military": military_radius_m,
        "industrial": industrial_radius_m,
        "power_plant": power_plant_radius_m,
        "airport": airport_radius_m,
        "aerodrome": aerodrome_radius_m,
        "airstrip_or_heliport": airstrip_heliport_radius_m,
        "nature_protection": nature_protection_radius_m,
        "landscape_protection": landscape_protection_radius_m,
    }

    input_path = Path(input_geojson)
    output_path = Path(output_geojson)

    if not input_path.exists():
        raise FileNotFoundError(f"Eingabedatei nicht gefunden: {input_path}")

    if polygon_corners < 3:
        raise ValueError("polygon_corners muss mindestens 3 sein.")

    if zone_buffer_resolution < 1:
        raise ValueError("zone_buffer_resolution muss mindestens 1 sein.")

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

        props = row.drop(labels=["geometry"]).to_dict()

        luftvo_type = classify_luftvo_type(props)

        if luftvo_type is None:
            continue

        radius_m = radius_by_type[luftvo_type]

        rows.append({
            **props,
            "luftvo_type": luftvo_type,
            "luftvo_radius_m": radius_m,
            "polygon_corners": polygon_corners,
            "zone_buffer_resolution": zone_buffer_resolution,
            "geometry": geom,
        })

    if not rows:
        raise ValueError("Keine passenden Features für LuftVO-Geometrien gefunden.")

    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84)

    # In metrisches CRS umwandeln, damit die Radien in Metern berechnet werden
    out_metric = out.to_crs(metric_crs)

    new_geometries = []

    for geom, radius_m in zip(out_metric.geometry, out_metric["luftvo_radius_m"]):

        if geom.geom_type in {"Point", "MultiPoint"}:
            # Punkt -> regelmäßiges Polygon mit n Ecken
            new_geom = create_point_polygon_geometry(
                geom=geom,
                radius_m=radius_m,
                corners=polygon_corners,
            )

        else:
            # Zone / Fläche / Linie -> um Radius vergrößern
            new_geom = create_buffered_zone_geometry(
                geom=geom,
                radius_m=radius_m,
                buffer_resolution=zone_buffer_resolution,
            )

        new_geometries.append(new_geom)

    out_metric["geometry"] = new_geometries

    # Ungültige Geometrien reparieren, falls nötig
    out_metric["geometry"] = out_metric["geometry"].buffer(0)

    # Zurück nach WGS84 für GeoJSON
    out_wgs84 = out_metric.to_crs(WGS84)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_wgs84.to_file(output_path, driver="GeoJSON")

    return out_wgs84


# ----------------------------------------
# Ab hier nur Ausführung als einzelne Datei
# ----------------------------------------

def main():
    input_file = "GeoDaten_Overpass.geojson"
    output_file = "drohnen_luftvo_kreise.geojson"

    result = create_luftvo_buffer_geojson(
        input_geojson=input_file,
        output_geojson=output_file,

        # Radien je Objektart aus der Tabelle
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

        # Naturschutzflächen / Landschaftsschutzflächen:
        # 0 bedeutet: Gebiet selbst verwenden, nicht zusätzlich puffern.
        nature_protection_radius_m=0,
        landscape_protection_radius_m=0,

        # n = Anzahl der Ecken für Punkt-Polygone
        polygon_corners=64,

        # Rundheit bei Puffern um bestehende Zonen
        zone_buffer_resolution=16,
    )

    print(f"Fertig: {output_file}")
    print(f"Anzahl erzeugter Geometrien: {len(result)}")


if __name__ == "__main__":
    main()