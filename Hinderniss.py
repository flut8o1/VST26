"""
Hinderniss.py – Interaktives Hinzufügen von Hindernissen per Mausklick.

Öffnet die Routenkarte als interaktives Fenster. Ein Linksklick auf die Karte
legt an dieser Stelle eine kreisförmige Sperrzone (Standardradius 100 m) an,
baut den Navigationsgraphen neu auf und sucht eine neue Route. Anschließend
wird die Karte aktualisiert.

Blockiert das neue Hindernis Start oder Ende oder macht es das Ziel
unerreichbar, wird es wieder verworfen und die vorige Route bleibt erhalten.

Start, Ende und alle Graph-Parameter werden vom Aufrufer übergeben
(siehe main.py).
"""

from time import perf_counter

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import xyzservices.providers as xyz
from shapely.geometry import Point

from Graph import create_navigation_graph
from Algorithmus import compute_route
from Visualisierung import build_route_geometries, draw_route_layers
from utils import WGS84, WEB_MERCATOR, DEFAULT_METRIC_CRS, get_fixed_extent_web_mercator


class InteractivePlanner:
    """
    Interaktive Routenkarte mit per Mausklick gesetzten Hindernissen.

    Hält die aktuellen Sperrzonen, baut bei jedem Klick den Graphen neu auf,
    sucht eine neue Route und zeichnet die Karte neu.
    """

    def __init__(
        self,
        *,
        zones,
        graph_kwargs,
        algorithm="astar",
        center_lat,
        center_lon,
        square_side_km,
        satellite_background=True,
        basemap_zoom=13,
        obstacle_radius_m=100,
        metric_crs=DEFAULT_METRIC_CRS,
    ):
        """
        zones:
            GeoDataFrame der Ausgangs-Sperrzonen (WGS84).
        graph_kwargs:
            Schlüsselwort-Argumente für create_navigation_graph
            (spacing_m, bbox_padding_m, max_bbox_wgs84, start_lat, …).
        algorithm:
            Suchalgorithmus ("dijkstra", "astar", "floyd_warshall").
        center_lat / center_lon / square_side_km:
            Fester Kartenausschnitt (wie bei der PNG-Ausgabe).
        """
        self.zones             = zones.copy()
        self.graph_kwargs      = dict(graph_kwargs)
        self.algorithm         = algorithm
        self.satellite         = satellite_background
        self.basemap_zoom      = basemap_zoom
        self.obstacle_radius_m = obstacle_radius_m
        self.metric_crs        = metric_crs

        # Fester Ausschnitt in Web Mercator (Achsen-Koordinatensystem).
        self.extent = get_fixed_extent_web_mercator(center_lat, center_lon, square_side_km)

        # Aktueller Graph und die Zeichengeometrien der Route.
        self.grid        = None
        self.route_geoms = None

        self.fig, self.ax = plt.subplots(figsize=(12, 12))
        self.fig.canvas.mpl_connect("button_press_event", self._on_click)

    # -------------------------------------------------------------------------
    # Graph + Route neu berechnen
    # -------------------------------------------------------------------------

    def _rebuild(self):
        """Graph aus self.zones neu bauen und Route neu suchen."""
        print("  Graph wird aufgebaut …", flush=True)
        grid = create_navigation_graph(zones=self.zones, **self.graph_kwargs)

        print("  Route wird gesucht …", flush=True)
        node_path, length_m = compute_route(grid, algorithm=self.algorithm)

        self.grid        = grid
        self.route_geoms = build_route_geometries(grid, node_path)
        return length_m, len(node_path)

    # -------------------------------------------------------------------------
    # Hindernis-Geometrie aus einem Mausklick
    # -------------------------------------------------------------------------

    def _make_obstacle(self, x_web_mercator, y_web_mercator):
        """
        Erzeugt eine kreisförmige Sperrzone (obstacle_radius_m) um einen Klick.

        Der Klickpunkt liegt in Web Mercator (Achsen-CRS). Für einen exakten
        Meter-Radius wird in das metrische CRS konvertiert, dort gepuffert und
        zurück nach WGS84 projiziert.
        """
        point_metric = (
            gpd.GeoSeries([Point(x_web_mercator, y_web_mercator)], crs=WEB_MERCATOR)
            .to_crs(self.metric_crs)
            .iloc[0]
        )
        circle_metric = point_metric.buffer(self.obstacle_radius_m)

        return (
            gpd.GeoSeries([circle_metric], crs=self.metric_crs)
            .to_crs(WGS84)
            .iloc[0]
        )

    # -------------------------------------------------------------------------
    # Zeichnen
    # -------------------------------------------------------------------------

    def _render(self, status=""):
        """Karte vollständig neu zeichnen (Hintergrund, Zonen, Graph, Route)."""
        ax = self.ax
        ax.cla()

        minx, miny, maxx, maxy = self.extent
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)

        if self.satellite:
            print("  Satellitenhintergrund wird geladen …", flush=True)
            cx.add_basemap(ax, source=xyz.Esri.WorldImagery, zoom=self.basemap_zoom)

        print("  Karte wird gezeichnet …", flush=True)
        route_line, route_points, start_point, end_point = self.route_geoms
        draw_route_layers(
            ax,
            grid=self.grid,
            zones=self.zones,
            route_line=route_line,
            route_points=route_points,
            start_point=start_point,
            end_point=end_point,
        )

        ax.set_axis_off()
        ax.set_aspect("equal")
        ax.set_title(
            status or "Linksklick auf die Karte fügt ein 100-m-Hindernis hinzu",
            fontsize=13,
        )
        self.fig.canvas.draw_idle()

    # -------------------------------------------------------------------------
    # Klick-Handler
    # -------------------------------------------------------------------------

    def _on_click(self, event):
        # Nur Linksklicks innerhalb der Karte berücksichtigen.
        if event.button != 1 or event.inaxes is not self.ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        # Pan/Zoom-Modus der Werkzeugleiste hat Vorrang – dann kein Hindernis.
        toolbar = self.fig.canvas.toolbar
        if toolbar is not None and getattr(toolbar, "mode", ""):
            return

        # Neues Hindernis als Zeile an die Zonen anhängen (vorigen Stand merken).
        circle = self._make_obstacle(event.xdata, event.ydata)
        new_row = gpd.GeoDataFrame(
            {"luftvo_type": ["hindernis"], "luftvo_radius_m": [self.obstacle_radius_m]},
            geometry=[circle],
            crs=WGS84,
        )

        prev_zones, prev_grid, prev_geoms = self.zones, self.grid, self.route_geoms

        self.zones = gpd.GeoDataFrame(
            pd.concat([self.zones, new_row], ignore_index=True), crs=WGS84
        )

        # Statusmeldung sofort anzeigen, dann neu rechnen.
        self._render(status="Hindernis gesetzt – Graph wird neu gebaut, Route wird gesucht …")
        plt.pause(0.01)

        try:
            length_m, _ = self._rebuild()
        except Exception as exc:
            # Hindernis blockiert Start/Ende oder macht das Ziel unerreichbar:
            # vorigen Zustand wiederherstellen und Hindernis verwerfen.
            self.zones, self.grid, self.route_geoms = prev_zones, prev_grid, prev_geoms
            self._render(status=f"Kein gültiger Weg ({exc}) – Hindernis verworfen.")
            return

        self._render(status=f"Neue Route: {length_m:.0f} m / {length_m / 1000:.3f} km")

    # -------------------------------------------------------------------------
    # Fenster öffnen
    # -------------------------------------------------------------------------

    def show(self):
        """Erste Route berechnen, Karte zeichnen und das Fenster öffnen."""
        print("Initiale Route wird berechnet …", flush=True)
        t0 = perf_counter()
        length_m, node_count = self._rebuild()
        laufzeit_s = perf_counter() - t0
        self._render(status=f"Route: {length_m:.0f} m / {length_m / 1000:.3f} km")
        print("Fenster wird geöffnet …", flush=True)
        plt.show()
        print("Fenster geschlossen.", flush=True)
        return {
            "algorithm":        self.algorithm,
            "route_length_m":   length_m,
            "route_length_km":  length_m / 1000,
            "route_node_count": node_count,
            "laufzeit_s":       laufzeit_s,
        }
