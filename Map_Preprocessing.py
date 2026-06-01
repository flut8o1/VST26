"""
Map_Preprocessing.py – Klassifizierung und Pufferung von LuftVO-Zonen.

Liest OSM-Rohdaten (GeoJSON, z. B. aus Overpass) ein, klassifiziert jedes
Feature anhand seiner OSM-Tags und erzeugt daraus gepufferte No-Fly-Zones
gemäß der deutschen Luftverkehrs-Ordnung (LuftVO).

Ausgabe: GeoDataFrame mit Polygon-Geometrien (WGS84), das direkt im
Speicher an die Graph-Erstellung weitergereicht wird – keine Zwischendatei.
"""

import geopandas as gpd

from utils import WGS84, DEFAULT_METRIC_CRS, read_geojson


# =============================================================================
# OSM-Tag-Normalisierung
# =============================================================================

def _norm(value):
    """Normalisiert einen OSM-Tag-Wert zu Kleinbuchstaben ohne Leerzeichen."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _get_tag(props, key):
    """Liest einen OSM-Tag aus den Feature-Properties."""
    return props.get(key)


# =============================================================================
# LuftVO-Klassifizierung
# =============================================================================

def classify_luftvo_type(props):
    """
    Bestimmt den LuftVO-Typ eines OSM-Features anhand seiner Tags.

    Rückgabe:
        str  – Typ-Bezeichner (z. B. "hospital", "airport", "military").
        None – Feature ist für die LuftVO nicht relevant und wird übersprungen.
    """
    # Alle relevanten Tags einmalig normalisieren, um wiederholte Aufrufe zu vermeiden.
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

    # --- Gesundheitseinrichtungen ---
    if amenity == "hospital" or healthcare == "hospital":
        return "hospital"

    # --- Sicherheitsbehörden ---
    if amenity == "police":
        return "police"

    if amenity == "prison":
        return "prison"

    # --- Diplomatische Einrichtungen ---
    if office in {"diplomatic", "embassy", "consulate"} or amenity in {"embassy", "consulate"}:
        return "diplomatic"

    # --- Behörden und Verwaltung ---
    # Townhall/Courthouse sind rechtlich grob, aber gebräuchlich als Annäherung.
    if office == "government" or amenity in {"townhall", "courthouse"}:
        return "government_or_security"

    # --- Militärische Anlagen ---
    if landuse == "military" or boundary == "military" or military:
        return "military"

    # --- Industrieanlagen ---
    if landuse == "industrial" or industrial or man_made == "works":
        return "industrial"

    # --- Energieanlagen ---
    if power == "plant":
        return "power_plant"

    # --- Naturschutzgebiete (inkl. Nationalparks, FFH, Vogelschutz) ---
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

    # --- Landschaftsschutzgebiete ---
    if (
        protect_class == "5"
        or short_protection_title == "lsg"
        or "landschaftsschutzgebiet" in designation
        or "isarauen" in name
        or "isarlandschaft" in name
    ):
        return "landscape_protection"

    # --- Flughäfen und Flugplätze ---
    # Großflughäfen werden durch IATA/ICAO-Code oder aerodrome:type identifiziert.
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

    # Feature ist für die LuftVO nicht relevant.
    return None


# =============================================================================
# Geometrie-Erzeugung
# =============================================================================

def _create_buffered_zone_geometry(geom, radius_m, buffer_resolution):
    """
    Vergrößert eine bestehende Geometrie (Polygon, Linie) um radius_m Meter.

    Bei radius_m = 0 wird die Geometrie unverändert zurückgegeben –
    typisch für Schutzgebiete, die bereits als Fläche vorliegen.
    """
    if radius_m == 0:
        return geom
    return geom.buffer(radius_m, resolution=buffer_resolution)


# =============================================================================
# Haupt-Pipeline-Funktion
# =============================================================================

def create_luftvo_buffer_geojson(
    input_geojson,
    *,
    # Sicherheitsradien je Objekttyp (in Metern)
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
    # Schutzgebiete werden nicht zusätzlich gepuffert (Fläche selbst = Zone)
    nature_protection_radius_m=0,
    landscape_protection_radius_m=0,
    # Qualität der erzeugten Polygone (Segmente pro Viertelkreis bei buffer())
    zone_buffer_resolution=16,
    metric_crs=DEFAULT_METRIC_CRS,
):
    """
    Liest OSM-GeoJSON ein und erzeugt eine gepufferte LuftVO-Zonenebene.

    Für jedes Feature wird der LuftVO-Typ bestimmt und eine Sicherheitszone
    in der entsprechenden Größe erzeugt. Alle Geometrietypen (Point, Linie,
    Polygon) werden einheitlich mit buffer() gepuffert. Bei Radius 0 wird
    die Geometrie unverändert übernommen (z. B. für Schutzgebiete).

    Rückgabe:
        GeoDataFrame mit den erzeugten Zonenflächen in WGS84.
    """

    # Radius-Lookup nach LuftVO-Typ für schnellen Zugriff in der Schleife.
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

    # --- Schritt 1: Eingabe einlesen und CRS normalisieren ---
    # read_geojson prüft Existenz und Leerheit und liefert WGS84.

    gdf = read_geojson(input_geojson)

    # --- Schritt 2: Features klassifizieren und relevante herausfiltern ---

    rows = []

    for _, row in gdf.iterrows():
        geom = row.geometry

        if geom is None or geom.is_empty:
            continue

        props       = row.drop(labels=["geometry"]).to_dict()
        luftvo_type = classify_luftvo_type(props)

        # Features ohne LuftVO-Relevanz überspringen.
        if luftvo_type is None:
            continue

        rows.append({
            **props,
            "luftvo_type":     luftvo_type,
            "luftvo_radius_m": radius_by_type[luftvo_type],
            "geometry":        geom,
        })

    if not rows:
        raise ValueError("Keine passenden Features für LuftVO-Geometrien gefunden.")

    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84)

    # --- Schritt 3: In metrisches CRS konvertieren ---
    # Puffer und Polygon-Radien werden in Metern berechnet,
    # was nur im projizierten metrischen CRS korrekt funktioniert.
    out_metric = out.to_crs(metric_crs)

    # --- Schritt 4: Sicherheitszonen erzeugen ---

    new_geometries = []

    for geom, radius_m in zip(out_metric.geometry, out_metric["luftvo_radius_m"]):
        new_geometries.append(_create_buffered_zone_geometry(geom, radius_m, zone_buffer_resolution))

    out_metric["geometry"] = new_geometries

    # buffer(0) repariert mögliche Topologie-Fehler in den erzeugten Geometrien.
    out_metric["geometry"] = out_metric["geometry"].buffer(0)

    # --- Schritt 5: Zurück nach WGS84 und im Speicher zurückgeben ---

    return out_metric.to_crs(WGS84)
