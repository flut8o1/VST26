from pathlib import Path
import webbrowser

import geopandas as gpd
import folium


WGS84 = "EPSG:4326"


def _style_feature(feature):
    """
    Styling für die GeoJSON-Flächen.
    Unterschiedliche Radien bekommen unterschiedliche Farben.
    """

    props = feature.get("properties", {})
    radius = props.get("luftvo_radius_m")

    if radius == 100:
        color = "#ff9800"
    elif radius == 1000:
        color = "#e53935"
    elif radius == 1500:
        color = "#8e24aa"
    else:
        color = "#1976d2"

    return {
        "fillColor": color,
        "color": color,
        "weight": 2,
        "fillOpacity": 0.35,
    }


def create_geojson_map(
    input_geojson,
    output_html="drohnen_luftvo_karte.html",
    *,
    open_in_browser=True,
):
    """
    Erstellt eine interaktive HTML-Karte aus einer GeoJSON-Datei.

    Parameter:
        input_geojson:
            Pfad zur GeoJSON-Datei, z. B. "drohnen_luftvo_kreise.geojson"

        output_html:
            Name der HTML-Datei, die erzeugt werden soll.

        open_in_browser:
            True = Karte nach dem Erstellen automatisch im Browser öffnen.

    Rückgabe:
        Pfad zur erzeugten HTML-Datei.
    """

    input_path = Path(input_geojson)
    output_path = Path(output_html)

    if not input_path.exists():
        raise FileNotFoundError(f"GeoJSON-Datei nicht gefunden: {input_path}")

    gdf = gpd.read_file(input_path)

    if gdf.empty:
        raise ValueError("Die GeoJSON-Datei enthält keine Features.")

    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    # Mittelpunkt für den Kartenstart berechnen
    bounds = gdf.total_bounds
    minx, miny, maxx, maxy = bounds

    center_lat = (miny + maxy) / 2
    center_lon = (minx + maxx) / 2

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=11,
        tiles="OpenStreetMap",
    )

    tooltip_fields = []
    tooltip_aliases = []

    for field, alias in [
        ("name", "Name"),
        ("luftvo_radius_m", "Radius in m"),
        ("luftvo_rule", "Regel"),
        ("amenity", "Amenity"),
        ("aeroway", "Aeroway"),
        ("office", "Office"),
        ("military", "Military"),
    ]:
        if field in gdf.columns:
            tooltip_fields.append(field)
            tooltip_aliases.append(alias)

    folium.GeoJson(
        data=gdf.to_json(),
        name="LuftVO-Drohnenkreise",
        style_function=_style_feature,
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
            localize=True,
            sticky=True,
        ) if tooltip_fields else None,
    ).add_to(m)

    # Karte automatisch auf alle Flächen zoomen
    m.fit_bounds([
        [miny, minx],
        [maxy, maxx],
    ])

    folium.LayerControl().add_to(m)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(output_path)

    if open_in_browser:
        webbrowser.open(output_path.resolve().as_uri())

    return output_path