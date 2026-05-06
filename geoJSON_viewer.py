"""
geoJSON_viewer.py – Visualisierung der LuftVO-Zonenflächen.

Stellt die gepufferten Sperrzonen als interaktive HTML-Karte (Folium)
oder als statische PNG-Karte (Matplotlib + optionaler Satellitenhintergrund) dar.

Die zentrale Einstiegsfunktion ist create_geojson_visualization(),
die je nach output_format an die passende Implementierung delegiert.
"""

import webbrowser

import folium
from matplotlib.patches import Patch

from utils import (
    WGS84, WEB_MERCATOR,
    read_geojson, get_fixed_extent_web_mercator,
    setup_map_figure, save_map_figure,
)


# =============================================================================
# Farb- und Bezeichnungs-Lookup nach LuftVO-Typ
# =============================================================================

# Farben für die Zonendarstellung – einheitlich für HTML- und PNG-Karten.
COLOR_BY_TYPE = {
    "hospital":               "#e53935",
    "police":                 "#1e88e5",
    "prison":                 "#6d4c41",
    "diplomatic":             "#3949ab",
    "government_or_security": "#fb8c00",
    "military":               "#4e342e",
    "industrial":             "#757575",
    "power_plant":            "#fdd835",
    "airport":                "#8e24aa",
    "aerodrome":              "#ab47bc",
    "airstrip_or_heliport":   "#26a69a",
    "nature_protection":      "#43a047",
    "landscape_protection":   "#7cb342",
}

# Deutsche Bezeichnungen für Legende und Tooltips.
LABEL_BY_TYPE = {
    "hospital":               "Krankenhaus",
    "police":                 "Polizei",
    "prison":                 "Gefängnis",
    "diplomatic":             "Diplomatisch",
    "government_or_security": "Behörde / Sicherheit",
    "military":               "Militär",
    "industrial":             "Industrie",
    "power_plant":            "Energieanlage",
    "airport":                "Flughafen",
    "aerodrome":              "Flugplatz",
    "airstrip_or_heliport":   "Heliport / Airstrip",
    "nature_protection":      "Naturschutz",
    "landscape_protection":   "Landschaftsschutz",
}


# =============================================================================
# HTML-Karte (Folium)
# =============================================================================

def _style_feature(feature):
    """
    Bestimmt das Folium-Styling für ein einzelnes GeoJSON-Feature.

    Schutzgebiete (radius = 0) werden etwas transparenter dargestellt,
    da sie die originale Fläche ohne zusätzlichen Puffer zeigen.
    """
    props      = feature.get("properties", {})
    luftvo_type = props.get("luftvo_type", "")
    radius     = props.get("luftvo_radius_m", 0)
    color      = COLOR_BY_TYPE.get(luftvo_type, "#1976d2")

    return {
        "fillColor":   color,
        "color":       color,
        "weight":      2,
        # Schutzgebiete (radius=0) etwas transparenter, da Originalfläche ohne Puffer.
        "fillOpacity": 0.25 if radius == 0 else 0.35,
    }


def create_geojson_html_map(input_geojson, output_html="drohnen_luftvo_karte.html", *, open_in_browser=True):
    """
    Erstellt eine interaktive HTML-Karte der LuftVO-Zonen mit Folium.

    Enthält Tooltips mit OSM-Attributen und eine Legende.

    open_in_browser:
        True – Karte wird nach dem Speichern im Browser geöffnet.
    """
    gdf = read_geojson(input_geojson)

    minx, miny, maxx, maxy = gdf.total_bounds
    center_lat = (miny + maxy) / 2
    center_lon = (minx + maxx) / 2

    m = folium.Map(location=[center_lat, center_lon], zoom_start=11, tiles="OpenStreetMap")

    # Nur Spalten als Tooltip anzeigen, die tatsächlich in der Datei vorhanden sind.
    possible_tooltip_fields = [
        ("name",                   "Name"),
        ("luftvo_type",            "Typ"),
        ("luftvo_radius_m",        "Radius in m"),
        ("polygon_corners",        "Polygon-Ecken"),
        ("amenity",                "Amenity"),
        ("healthcare",             "Healthcare"),
        ("aeroway",                "Aeroway"),
        ("office",                 "Office"),
        ("landuse",                "Landuse"),
        ("military",               "Military"),
        ("power",                  "Power"),
        ("boundary",               "Boundary"),
        ("protect_class",          "Protect Class"),
        ("short_protection_title", "Schutzgebiet"),
    ]

    tooltip_fields  = [f for f, _ in possible_tooltip_fields if f in gdf.columns]
    tooltip_aliases = [a for f, a in possible_tooltip_fields if f in gdf.columns]

    tooltip = (
        folium.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases, localize=True, sticky=True)
        if tooltip_fields else None
    )

    folium.GeoJson(
        data=gdf.to_json(default=str),
        name="LuftVO-Geozonen",
        style_function=_style_feature,
        tooltip=tooltip,
    ).add_to(m)

    # Statische HTML-Legende unten links in der Karte.
    legend_html = """
    <div style="
        position: fixed; bottom: 40px; left: 40px; width: 260px;
        z-index: 9999; background-color: white; border: 2px solid grey;
        border-radius: 6px; padding: 10px; font-size: 14px;">
        <b>Legende</b><br>
        <span style="color:#e53935;">■</span> Krankenhaus<br>
        <span style="color:#1e88e5;">■</span> Polizei<br>
        <span style="color:#6d4c41;">■</span> Gefängnis<br>
        <span style="color:#3949ab;">■</span> Diplomatisch<br>
        <span style="color:#fb8c00;">■</span> Behörde / Sicherheit<br>
        <span style="color:#4e342e;">■</span> Militär<br>
        <span style="color:#757575;">■</span> Industrie<br>
        <span style="color:#fdd835;">■</span> Energieanlage<br>
        <span style="color:#8e24aa;">■</span> Flughafen<br>
        <span style="color:#ab47bc;">■</span> Flugplatz<br>
        <span style="color:#26a69a;">■</span> Heliport / Airstrip<br>
        <span style="color:#43a047;">■</span> Naturschutz<br>
        <span style="color:#7cb342;">■</span> Landschaftsschutz<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    m.fit_bounds([[miny, minx], [maxy, maxx]])
    folium.LayerControl().add_to(m)

    from pathlib import Path
    output_path = Path(output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(output_path)

    if open_in_browser:
        webbrowser.open(output_path.resolve().as_uri())

    return output_path


# =============================================================================
# PNG-Karte (Matplotlib)
# =============================================================================

def create_geojson_png_map(
    input_geojson,
    output_png="drohnen_luftvo_karte.png",
    *,
    show_map=True,
    satellite_background=True,
    basemap_zoom=13,
    fixed_png_extent=False,
    png_center_lat=48.137154,
    png_center_lon=11.576124,
    png_square_side_km=25,
):
    """
    Erstellt eine statische PNG-Karte der LuftVO-Zonen.

    Zonen werden farblich nach Typ unterschieden; eine Legende wird
    automatisch erstellt. Optionaler Satelliten-Hintergrund via Esri.

    fixed_png_extent:
        True  – Fester Quadratausschnitt um den angegebenen Mittelpunkt.
        False – Ausschnitt wird aus den Zonengrenzen abgeleitet.
    """
    gdf = read_geojson(input_geojson)

    if "luftvo_type" not in gdf.columns:
        raise ValueError("Spalte 'luftvo_type' fehlt in der GeoJSON-Datei.")

    # Für Webkarten-Hintergründe muss in Web Mercator projiziert werden.
    gdf_plot = gdf.to_crs(WEB_MERCATOR)

    # Kartenausschnitt bestimmen.
    if fixed_png_extent:
        minx, miny, maxx, maxy = get_fixed_extent_web_mercator(
            center_lat=png_center_lat,
            center_lon=png_center_lon,
            square_side_km=png_square_side_km,
        )
    else:
        minx, miny, maxx, maxy = gdf_plot.total_bounds

    fig, ax = setup_map_figure(
        minx, miny, maxx, maxy,
        satellite_background=satellite_background,
        basemap_zoom=basemap_zoom,
    )

    # Jede Zonenart in ihrer Farbe plotten und Legendeneinträge sammeln.
    legend_items = []

    for luftvo_type in sorted(gdf_plot["luftvo_type"].dropna().unique()):
        subset = gdf_plot[gdf_plot["luftvo_type"] == luftvo_type]
        color  = COLOR_BY_TYPE.get(luftvo_type, "#1976d2")
        label  = LABEL_BY_TYPE.get(luftvo_type, luftvo_type)

        subset.plot(ax=ax, facecolor=color, edgecolor=color, linewidth=1.2, alpha=0.35, zorder=2)
        legend_items.append(Patch(facecolor=color, edgecolor=color, label=label, alpha=0.35))

    if legend_items:
        ax.legend(handles=legend_items, loc="upper right", fontsize=9, frameon=True)

    # Kartentitel – verwendet den konfigurierten Wert für die Quadratgröße.
    if fixed_png_extent:
        title = f"LuftVO-Geozonen – {png_square_side_km} km Quadrat um München Stadtmitte"
    else:
        title = "LuftVO-Geozonen mit Satellitenhintergrund"

    return save_map_figure(fig, ax, output_png, title=title, show_map=show_map)


# =============================================================================
# Zentrale Visualisierungsfunktion
# =============================================================================

def create_geojson_visualization(
    input_geojson,
    output_path,
    *,
    output_format="html",
    open_in_browser=True,
    show_png=True,
    satellite_background=True,
    basemap_zoom=13,
    fixed_png_extent=False,
    png_center_lat=48.137154,
    png_center_lon=11.576124,
    png_square_side_km=25,
):
    """
    Zentrale Einstiegsfunktion für die Zonenvisualisierung.

    output_format:
        "html" – Interaktive Folium-Karte.
        "png"  – Statische Matplotlib-Karte.
    """
    output_format = output_format.lower().strip()

    if output_format == "html":
        return create_geojson_html_map(
            input_geojson=input_geojson,
            output_html=output_path,
            open_in_browser=open_in_browser,
        )

    if output_format == "png":
        return create_geojson_png_map(
            input_geojson=input_geojson,
            output_png=output_path,
            show_map=show_png,
            satellite_background=satellite_background,
            basemap_zoom=basemap_zoom,
            fixed_png_extent=fixed_png_extent,
            png_center_lat=png_center_lat,
            png_center_lon=png_center_lon,
            png_square_side_km=png_square_side_km,
        )

    raise ValueError("output_format muss 'html' oder 'png' sein.")
