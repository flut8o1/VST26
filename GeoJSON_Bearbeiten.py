from pathlib import Path
from math import cos, sin, pi

import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon

from utils import WGS84, DEFAULT_METRIC_CRS


def _norm(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def _get_tag(props, key):
    if key in props:
        return props.get(key)
    tags = props.get("tags")
    if isinstance(tags, dict):
        return tags.get(key)
    return None


def classify_luftvo_type(props):
    amenity                = _norm(_get_tag(props, "amenity"))
    healthcare             = _norm(_get_tag(props, "healthcare"))
    office                 = _norm(_get_tag(props, "office"))
    aeroway                = _norm(_get_tag(props, "aeroway"))
    landuse                = _norm(_get_tag(props, "landuse"))
    boundary               = _norm(_get_tag(props, "boundary"))
    military               = _norm(_get_tag(props, "military"))
    industrial             = _norm(_get_tag(props, "industrial"))
    man_made               = _norm(_get_tag(props, "man_made"))
    power                  = _norm(_get_tag(props, "power"))
    protect_class          = _norm(_get_tag(props, "protect_class"))
    designation            = _norm(_get_tag(props, "designation"))
    short_protection_title = _norm(_get_tag(props, "short_protection_title"))
    name                   = _norm(_get_tag(props, "name"))
    aerodrome_type         = _norm(_get_tag(props, "aerodrome:type"))
    aerodrome              = _norm(_get_tag(props, "aerodrome"))
    iata                   = _norm(_get_tag(props, "iata"))
    icao                   = _norm(_get_tag(props, "icao"))

    if amenity == "hospital" or healthcare == "hospital":
        return "hospital"
    if amenity == "police":
        return "police"
    if amenity == "prison":
        return "prison"
    if office in {"diplomatic", "embassy", "consulate"} or amenity in {"embassy", "consulate"}:
        return "diplomatic"
    if office == "government" or amenity in {"townhall", "courthouse"}:
        return "government_or_security"
    if landuse == "military" or boundary == "military" or military:
        return "military"
    if landuse == "industrial" or industrial or man_made == "works":
        return "industrial"
    if power == "plant":
        return "power_plant"
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
    if (
        protect_class == "5"
        or short_protection_title == "lsg"
        or "landschaftsschutzgebiet" in designation
        or "isarauen" in name
        or "isarlandschaft" in name
    ):
        return "landscape_protection"

    airport_indicators = {
        "airport", "international", "regional", "commercial",
        "major", "large_airport", "medium_airport",
    }
    if aeroway == "aerodrome":
        if aerodrome_type in airport_indicators or aerodrome in airport_indicators or iata or icao:
            return "airport"
        return "aerodrome"
    if aeroway in {"airstrip", "heliport", "helipad"}:
        return "airstrip_or_heliport"

    return None


def _create_regular_polygon(point, radius_m, corners):
    if corners < 3:
        raise ValueError("polygon_corners muss mindestens 3 sein.")
    x, y = point.x, point.y
    coords = [
        (x + radius_m * cos(2 * pi * i / corners),
         y + radius_m * sin(2 * pi * i / corners))
        for i in range(corners)
    ]
    coords.append(coords[0])
    return Polygon(coords)


def _create_point_polygon_geometry(geom, radius_m, corners):
    if geom.geom_type == "Point":
        return _create_regular_polygon(geom, radius_m, corners)
    if geom.geom_type == "MultiPoint":
        return MultiPolygon([
            _create_regular_polygon(pt, radius_m, corners)
            for pt in geom.geoms
        ])
    raise ValueError(f"Nicht unterstützte Punkt-Geometrie: {geom.geom_type}")


def _create_buffered_zone_geometry(geom, radius_m, buffer_resolution):
    if radius_m == 0:
        return geom
    return geom.buffer(radius_m, resolution=buffer_resolution)


def create_luftvo_buffer_geojson(
    input_geojson,
    output_geojson,
    *,
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
    polygon_corners=64,
    zone_buffer_resolution=16,
    metric_crs=DEFAULT_METRIC_CRS,
):
    radius_by_type = {
        "hospital":               hospital_radius_m,
        "police":                 police_radius_m,
        "prison":                 prison_radius_m,
        "diplomatic":             diplomatic_radius_m,
        "government_or_security": government_radius_m,
        "military":               military_radius_m,
        "industrial":             industrial_radius_m,
        "power_plant":            power_plant_radius_m,
        "airport":                airport_radius_m,
        "aerodrome":              aerodrome_radius_m,
        "airstrip_or_heliport":   airstrip_heliport_radius_m,
        "nature_protection":      nature_protection_radius_m,
        "landscape_protection":   landscape_protection_radius_m,
    }

    input_path  = Path(input_geojson)
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
    gdf = gdf.set_crs(WGS84) if gdf.crs is None else gdf.to_crs(WGS84)

    rows = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props       = row.drop(labels=["geometry"]).to_dict()
        luftvo_type = classify_luftvo_type(props)
        if luftvo_type is None:
            continue
        rows.append({
            **props,
            "luftvo_type":            luftvo_type,
            "luftvo_radius_m":        radius_by_type[luftvo_type],
            "polygon_corners":        polygon_corners,
            "zone_buffer_resolution": zone_buffer_resolution,
            "geometry":               geom,
        })

    if not rows:
        raise ValueError("Keine passenden Features für LuftVO-Geometrien gefunden.")

    out        = gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84)
    out_metric = out.to_crs(metric_crs)

    new_geometries = []
    for geom, radius_m in zip(out_metric.geometry, out_metric["luftvo_radius_m"]):
        if geom.geom_type in {"Point", "MultiPoint"}:
            new_geom = _create_point_polygon_geometry(geom, radius_m, polygon_corners)
        else:
            new_geom = _create_buffered_zone_geometry(geom, radius_m, zone_buffer_resolution)
        new_geometries.append(new_geom)

    out_metric["geometry"] = new_geometries
    out_metric["geometry"] = out_metric["geometry"].buffer(0)

    out_wgs84 = out_metric.to_crs(WGS84)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_wgs84.to_file(output_path, driver="GeoJSON")

    return out_wgs84
