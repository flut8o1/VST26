from pathlib import Path
import webbrowser

import geopandas as gpd
import folium
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import contextily as cx
import xyzservices.providers as xyz


WGS84 = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"


COLOR_BY_TYPE = {
    "hospital": "#e53935",
    "police": "#1e88e5",
    "prison": "#6d4c41",
    "diplomatic": "#3949ab",
    "government_or_security": "#fb8c00",
    "military": "#4e342e",
    "industrial": "#757575",
    "power_plant": "#fdd835",
    "airport": "#8e24aa",
    "aerodrome": "#ab47bc",
    "airstrip_or_heliport": "#26a69a",
    "nature_protection": "#43a047",
    "landscape_protection": "#7cb342",
}


LABEL_BY_TYPE = {
    "hospital": "Krankenhaus",
    "police": "Polizei",
    "prison": "Gefängnis",
    "diplomatic": "Diplomatisch",
    "government_or_security": "Behörde / Sicherheit",
    "military": "Militär",
    "industrial": "Industrie",
    "power_plant": "Energieanlage",
    "airport": "Flughafen",
    "aerodrome": "Flugplatz",
    "airstrip_or_heliport": "Heliport / Airstrip",
    "nature_protection": "Naturschutz",
    "landscape_protection": "Landschaftsschutz",
}


def _style_feature(feature):
    """
    Styling für die HTML-Karte mit Folium.
    """

    props = feature.get("properties", {})
    luftvo_type = props.get("luftvo_type", "")
    radius = props.get("luftvo_radius_m", 0)

    color = COLOR_BY_TYPE.get(luftvo_type, "#1976d2")

    if radius == 0:
        fill_opacity = 0.25
    else:
        fill_opacity = 0.35

    return {
        "fillColor": color,
        "color": color,
        "weight": 2,
        "fillOpacity": fill_opacity,
    }


def create_geojson_html_map(
    input_geojson,
    output_html="drohnen_luftvo_karte.html",
    *,
    open_in_browser=True,
):
    """
    Erstellt eine interaktive HTML-Karte mit Folium.
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

    minx, miny, maxx, maxy = gdf.total_bounds

    center_lat = (miny + maxy) / 2
    center_lon = (minx + maxx) / 2

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=11,
        tiles="OpenStreetMap",
    )

    possible_tooltip_fields = [
        ("name", "Name"),
        ("luftvo_type", "Typ"),
        ("luftvo_radius_m", "Radius in m"),
        ("polygon_corners", "Polygon-Ecken"),
        ("amenity", "Amenity"),
        ("healthcare", "Healthcare"),
        ("aeroway", "Aeroway"),
        ("office", "Office"),
        ("landuse", "Landuse"),
        ("military", "Military"),
        ("power", "Power"),
        ("boundary", "Boundary"),
        ("protect_class", "Protect Class"),
        ("short_protection_title", "Schutzgebiet"),
    ]

    tooltip_fields = []
    tooltip_aliases = []

    for field, alias in possible_tooltip_fields:
        if field in gdf.columns:
            tooltip_fields.append(field)
            tooltip_aliases.append(alias)

    tooltip = None

    if tooltip_fields:
        tooltip = folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
            localize=True,
            sticky=True,
        )

    folium.GeoJson(
        data=gdf.to_json(default=str),
        name="LuftVO-Geozonen",
        style_function=_style_feature,
        tooltip=tooltip,
    ).add_to(m)

    legend_html = """
    <div style="
        position: fixed;
        bottom: 40px;
        left: 40px;
        width: 260px;
        z-index: 9999;
        background-color: white;
        border: 2px solid grey;
        border-radius: 6px;
        padding: 10px;
        font-size: 14px;
    ">
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


def create_geojson_png_map(
    input_geojson,
    output_png="drohnen_luftvo_karte.png",
    *,
    show_map=True,
    satellite_background=True,
    basemap_zoom=13,
):
    """
    Erstellt eine statische PNG-Karte mit optionalem Satellitenhintergrund.

    satellite_background=True:
        Fügt ein Satellitenbild als Hintergrund hinzu.

    basemap_zoom:
        Zoomstufe der Hintergrundkacheln.
        Höher = detaillierter, aber langsamer.
    """

    input_path = Path(input_geojson)
    output_path = Path(output_png)

    if not input_path.exists():
        raise FileNotFoundError(f"GeoJSON-Datei nicht gefunden: {input_path}")

    gdf = gpd.read_file(input_path)

    if gdf.empty:
        raise ValueError("Die GeoJSON-Datei enthält keine Features.")

    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    if "luftvo_type" not in gdf.columns:
        raise ValueError("Spalte 'luftvo_type' fehlt in der GeoJSON-Datei.")

    # Für Satelliten-/Webkarten muss nach EPSG:3857 umgerechnet werden
    gdf_plot = gdf.to_crs(WEB_MERCATOR)

    minx, miny, maxx, maxy = gdf_plot.total_bounds

    fig, ax = plt.subplots(figsize=(14, 14))

    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)

    # Satelliten-Hintergrund zuerst zeichnen
    if satellite_background:
        cx.add_basemap(
            ax,
            source=xyz.Esri.WorldImagery,
            zoom=basemap_zoom,
        )

    legend_items = []

    # LuftVO-Zonen über den Satellitenhintergrund legen
    for luftvo_type in sorted(gdf_plot["luftvo_type"].dropna().unique()):
        subset = gdf_plot[gdf_plot["luftvo_type"] == luftvo_type]

        color = COLOR_BY_TYPE.get(luftvo_type, "#1976d2")
        label = LABEL_BY_TYPE.get(luftvo_type, luftvo_type)

        subset.plot(
            ax=ax,
            facecolor=color,
            edgecolor=color,
            linewidth=1.2,
            alpha=0.35,
            zorder=2,
        )

        legend_items.append(
            Patch(
                facecolor=color,
                edgecolor=color,
                label=label,
                alpha=0.35,
            )
        )

    # Ausschnitt nach dem Plotten nochmal fixieren
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)

    ax.set_title("LuftVO-Geozonen mit Satellitenhintergrund", fontsize=16)
    ax.set_axis_off()
    ax.set_aspect("equal")

    if legend_items:
        ax.legend(
            handles=legend_items,
            loc="upper right",
            fontsize=9,
            frameon=True,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")

    if show_map:
        plt.show()
    else:
        plt.close(fig)

    return output_path


def create_geojson_visualization(
    input_geojson,
    output_path,
    *,
    output_format="html",
    open_in_browser=True,
    show_png=True,
    satellite_background=True,
    basemap_zoom=13,
):
    """
    Zentrale Visualisierungsfunktion.

    output_format:
        "html" oder "png"
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
        )

    raise ValueError("output_format muss 'html' oder 'png' sein.")


def main():
    input_geojson = "drohnen_luftvo_zonen.geojson"

    output_format = "png"

    if output_format == "html":
        output_file = "drohnen_luftvo_karte.html"
    elif output_format == "png":
        output_file = "drohnen_luftvo_karte.png"
    else:
        raise ValueError("output_format muss 'html' oder 'png' sein.")

    result_file = create_geojson_visualization(
        input_geojson=input_geojson,
        output_path=output_file,
        output_format=output_format,
        open_in_browser=True,
        show_png=True,
        satellite_background=True,
        basemap_zoom=13,
    )

    print(f"Karte wurde erstellt: {result_file}")


if __name__ == "__main__":
    main()