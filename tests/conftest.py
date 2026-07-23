"""Fixtures e utilitarios para os testes.

Inclui um construtor de malha viaria SINTETICA (em memoria), que permite testar
o map matching de forma deterministica e offline - simulando ruas paralelas,
cruzamentos, retornos e vias sem nome sem depender do OpenStreetMap.
"""
from __future__ import annotations

import datetime as _dt
import math
import sys
from pathlib import Path

import networkx as nx
import pytest
from shapely.geometry import LineString

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from src import config_loader, utils  # noqa: E402
from src.coordinate_system import CoordinateSystem  # noqa: E402
from src.models import TrackPoint  # noqa: E402
from src.road_matcher import GrafoVias  # noqa: E402

LAT0 = -3.02
LON0 = -59.94


def lonlat(east_m: float, north_m: float) -> tuple[float, float]:
    """Converte deslocamento local (metros) para (lon, lat) perto da base."""
    lat = LAT0 + north_m / 111_320.0
    lon = LON0 + east_m / (111_320.0 * math.cos(math.radians(LAT0)))
    return lon, lat


def construir_grafo(roads: list[dict]) -> nx.MultiDiGraph:
    """Constroi um MultiDiGraph no estilo OSMnx a partir de uma descricao de ruas.

    Cada rua: {"name": str|None, "highway": str, "nodes": [(east,north), ...],
               "oneway": bool, "osmid": int}
    """
    G = nx.MultiDiGraph()
    G.graph["crs"] = "epsg:4326"
    ids: dict[tuple, int] = {}

    def nid(e, n) -> int:
        key = (round(e, 3), round(n, 3))
        if key not in ids:
            i = len(ids) + 1
            lon, lat = lonlat(e, n)
            G.add_node(i, x=lon, y=lat)
            ids[key] = i
        return ids[key]

    osmid = 1000
    for r in roads:
        pts = r["nodes"]
        oneway = r.get("oneway", False)
        for a, b in zip(pts[:-1], pts[1:]):
            ua, vb = nid(*a), nid(*b)
            la, lb = lonlat(*a), lonlat(*b)
            length = utils.haversine_m(la[1], la[0], lb[1], lb[0])
            attrs = {
                "osmid": r.get("osmid", osmid),
                "highway": r.get("highway", "residential"),
                "length": length, "oneway": oneway,
                "geometry": LineString([la, lb]),
            }
            if r.get("name") is not None:
                attrs["name"] = r["name"]
            G.add_edge(ua, vb, **attrs)
            if not oneway:
                rev = dict(attrs)
                rev["geometry"] = LineString([lb, la])
                G.add_edge(vb, ua, **rev)
            osmid += 1
    return G


def grafo_vias(roads: list[dict]) -> GrafoVias:
    G = construir_grafo(roads)
    coords = [(d["x"], d["y"]) for _, d in G.nodes(data=True)]
    cs = CoordinateSystem.para_pontos(coords)
    return GrafoVias(G, cs)


def pontos_ao_longo(caminho_en: list[tuple[float, float]], passo_m: float = 5.0,
                    ruido_lat_m: float = 0.0, dt_s: float = 1.0,
                    com_tempo: bool = True) -> list[TrackPoint]:
    """Gera TrackPoints ao longo de um caminho (lista de pontos east/north em m).

    ruido_lat_m desloca lateralmente cada ponto (simula erro de GPS).
    """
    from src import gpx_reader
    amostras: list[tuple[float, float]] = []
    for a, b in zip(caminho_en[:-1], caminho_en[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        dist = math.hypot(dx, dy)
        n = max(1, int(dist // passo_m))
        ux, uy = dx / dist, dy / dist
        px, py = -uy, ux  # perpendicular unitario
        for i in range(n):
            t = i * passo_m
            ex = a[0] + ux * t + px * ruido_lat_m
            en = a[1] + uy * t + py * ruido_lat_m
            amostras.append((ex, en))
    amostras.append(caminho_en[-1])

    t0 = _dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
    pts: list[TrackPoint] = []
    for i, (e, n) in enumerate(amostras):
        lon, lat = lonlat(e, n)
        pts.append(TrackPoint(
            indice=i, lat=lat, lon=lon,
            tempo=(t0 + _dt.timedelta(seconds=i * dt_s)) if com_tempo else None,
            hdop=1.5))
    from src import gpx_reader as gr
    gr.recomputar_metricas(pts)
    return pts


@pytest.fixture
def cfg():
    return config_loader.carregar_config()


@pytest.fixture
def gpx_teste_path() -> Path | None:
    """Caminho do GPX de teste, se disponivel no ambiente."""
    p = Path(r"C:\Users\CATTER_ENGENHARIA\Downloads\GX152586_1_GPS5.gpx")
    return p if p.exists() else None
