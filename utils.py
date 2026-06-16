"""
utils.py – Gemeinsam genutzte Konstanten und Hilfsfunktionen.

Dieses Modul bündelt alle Definitionen, die von mehreren anderen Modulen
genutzt werden:
  - CRS-Konstanten
  - GeoJSON einlesen
  - Geometrie-Operationen
  - Matplotlib-Kartenhelfer (Figure erstellen und speichern)

Alle anderen Module importieren aus diesem Modul, statt die Logik
selbst zu duplizieren.
"""

from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import xyzservices.providers as xyz
from shapely.geometry import Point
from shapely.prepared import prep


# =============================================================================
# Koordinatenreferenzsysteme (CRS)
# =============================================================================

# Geographische Koordinaten (Längen-/Breitengrad) – Standard für GeoJSON.
WGS84 = "EPSG:4326"

# Metrische Web-Projektion – wird von Kachel-Hintergründen (Esri, OSM) benötigt.
WEB_MERCATOR = "EPSG:3857"

# UTM Zone 32N – für den Raum München optimal, da Meter-Abstände korrekt sind.
DEFAULT_METRIC_CRS = "EPSG:25832"


# =============================================================================
# GeoJSON-IO
# =============================================================================

def read_geojson(file_path):
    """
    Liest eine GeoJSON-Datei ein und gibt ein GeoDataFrame in WGS84 zurück.

    Falls die Datei kein CRS enthält, wird WGS84 angenommen.

    Wirft:
        FileNotFoundError – Datei existiert nicht.
        ValueError        – Datei enthält keine Features.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {path}")

    gdf = gpd.read_file(path)

    if gdf.empty:
        raise ValueError(f"Datei enthält keine Features: {path}")

    # GeoJSON sollte immer WGS84 sein; zur Sicherheit explizit setzen/konvertieren.
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    return gdf


# =============================================================================
# Geometrie-Hilfsfunktionen
# =============================================================================

def prepare_geometry(geom):
    """
    Erstellt eine vorberechnete Geometrie für schnelle wiederholte Schnitt-Tests.

    Shapely's prep() bereitet die Geometrie intern für wiederholte Schnitt-Tests
    vor, sodass jeder folgende intersects()-Aufruf gegen dieselbe Geometrie
    deutlich schneller ist als ohne Vorbereitung.

    Besonders nützlich, wenn die Sperrzonen-Gesamtfläche für tausende
    Knoten- und Kantentests verwendet wird.
    """
    return prep(geom)


def get_geometry_union(gdf):
    """
    Vereinigt alle Geometrien eines GeoDataFrames zu einer einzigen Geometrie.

    Unterstützt ältere GeoPandas-Versionen (unary_union) und
    neuere (union_all) gleichermaßen.
    """
    try:
        return gdf.geometry.union_all()
    except AttributeError:
        # Fallback für GeoPandas < 0.14
        return gdf.geometry.unary_union


def wgs84_to_metric(lat, lon, metric_crs):
    """
    Konvertiert einen WGS84-Punkt in das angegebene metrische CRS.

    lat / lon:
        Geographische Koordinaten in WGS84.
    metric_crs:
        Ziel-CRS, z. B. EPSG:25832.

    Rückgabe:
        Shapely Point im metrischen CRS.
    """
    point_wgs84 = gpd.GeoSeries([Point(lon, lat)], crs=WGS84)
    return point_wgs84.to_crs(metric_crs).iloc[0]


# =============================================================================
# Kartenausschnitt
# =============================================================================

def get_fixed_extent_web_mercator(center_lat, center_lon, square_side_km):
    """
    Berechnet einen quadratischen Kartenausschnitt in Web Mercator (EPSG:3857).

    center_lat / center_lon:
        Mittelpunkt des Ausschnitts in WGS84.
    square_side_km:
        Seitenlänge des Quadrats in Kilometern.

    Rückgabe:
        (minx, miny, maxx, maxy) in EPSG:3857-Koordinaten.
    """
    center = (
        gpd.GeoSeries([Point(center_lon, center_lat)], crs=WGS84)
        .to_crs(WEB_MERCATOR)
        .iloc[0]
    )
    half_m = square_side_km * 1000 / 2

    return (
        center.x - half_m,
        center.y - half_m,
        center.x + half_m,
        center.y + half_m,
    )


# =============================================================================
# Matplotlib-Kartenhelfer
# =============================================================================

def setup_map_figure(minx, miny, maxx, maxy, *, satellite_background=True, basemap_zoom=13):
    """
    Erstellt eine Matplotlib-Figure für eine georeferenzierte Karte.

    Setzt den Kartenausschnitt und lädt optional den Satelliten-Hintergrund
    (Esri World Imagery) via contextily.

    Der Ausschnitt wird vor dem Basemap-Laden gesetzt, damit contextily
    genau die richtigen Kacheln für den sichtbaren Bereich anfordert.

    Rückgabe:
        (fig, ax) – Matplotlib Figure und Axes, bereit zum Beplottten.
    """
    fig, ax = plt.subplots(figsize=(14, 14))

    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)

    if satellite_background:
        cx.add_basemap(ax, source=xyz.Esri.WorldImagery, zoom=basemap_zoom)

    return fig, ax


def save_map_figure(fig, ax, output_path, *, title="", show_map=True):
    """
    Finalisiert und speichert eine Matplotlib-Figure als PNG.

    Setzt Titel, entfernt Achsenbeschriftungen, erzwingt gleiche Skalierung
    und speichert die Datei mit 300 DPI.

    title:
        Kartentitel (leer = kein Titel).
    show_map:
        True  – Karte wird nach dem Speichern angezeigt.
        False – Figure wird ohne Anzeige geschlossen.

    Rückgabe:
        Path-Objekt der gespeicherten PNG-Datei.
    """
    if title:
        ax.set_title(title, fontsize=16)

    ax.set_axis_off()
    ax.set_aspect("equal")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")

    if show_map:
        plt.show()
    else:
        plt.close(fig)

    return output_path
