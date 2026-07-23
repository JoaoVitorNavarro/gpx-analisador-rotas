"""Cache territorial por tiles (quadrantes) determinísticos.

O territorio e dividido em uma grade fixa de tiles de ~5 km (configuravel). Cada
tile tem um identificador deterministico derivado das coordenadas, de modo que
o mesmo pedaco de mundo sempre cai no mesmo tile - permitindo reutilizacao entre
videos e evitando baixar a mesma regiao duas vezes.

Formato do identificador:  t{tamanho_km}k_{ix}_{iy}
onde ix = floor(lon/passo), iy = floor(lat/passo) e passo = tamanho_km/111.32
(graus). Ex.: t5k_-1335_-68. O identificador e reversivel para os limites.

A malha viaria de cada tile e salva em GraphML (para recarregar no OSMnx) e,
opcionalmente, em GeoPackage (para inspecao). O catalogo fica no SQLite.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from . import utils
from .coordinate_system import BBox

logger = utils.get_logger()

GRAUS_POR_KM = 1.0 / 111.32   # aproximacao suficiente para dimensionar tiles


@dataclass(frozen=True)
class Tile:
    ix: int
    iy: int
    tamanho_km: float

    @property
    def passo(self) -> float:
        return self.tamanho_km * GRAUS_POR_KM

    @property
    def id(self) -> str:
        return f"t{_fmt_km(self.tamanho_km)}k_{self.ix}_{self.iy}"

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(lon_min, lat_min, lon_max, lat_max)."""
        p = self.passo
        return (self.ix * p, self.iy * p, (self.ix + 1) * p, (self.iy + 1) * p)

    @property
    def bbox(self) -> BBox:
        lo0, la0, lo1, la1 = self.bounds
        return BBox(lo0, la0, lo1, la1)

    def caminho_grafo(self) -> Path:
        return utils.cache_dir() / f"{self.id}.graphml"

    def caminho_gpkg(self) -> Path:
        return utils.cache_dir() / f"{self.id}.gpkg"


def _fmt_km(km: float) -> str:
    return f"{km:g}"


def tile_de(lon: float, lat: float, tamanho_km: float) -> Tile:
    passo = tamanho_km * GRAUS_POR_KM
    return Tile(ix=math.floor(lon / passo), iy=math.floor(lat / passo),
                tamanho_km=tamanho_km)


def tiles_para_bbox(bbox: BBox, tamanho_km: float, buffer_m: float = 0.0) -> list[Tile]:
    """Retorna todos os tiles que cobrem o bbox (opcionalmente expandido)."""
    passo = tamanho_km * GRAUS_POR_KM
    graus_buffer_lat = buffer_m / 111_320.0
    lat_c = (bbox.lat_min + bbox.lat_max) / 2.0
    graus_buffer_lon = buffer_m / (111_320.0 * max(0.1, math.cos(math.radians(lat_c))))

    lo0 = bbox.lon_min - graus_buffer_lon
    la0 = bbox.lat_min - graus_buffer_lat
    lo1 = bbox.lon_max + graus_buffer_lon
    la1 = bbox.lat_max + graus_buffer_lat

    ix0, ix1 = math.floor(lo0 / passo), math.floor(lo1 / passo)
    iy0, iy1 = math.floor(la0 / passo), math.floor(la1 / passo)
    tiles = [Tile(ix, iy, tamanho_km)
             for ix in range(ix0, ix1 + 1)
             for iy in range(iy0, iy1 + 1)]
    return tiles


def tiles_para_pontos(lonlats, tamanho_km: float, buffer_m: float = 0.0) -> list[Tile]:
    if not lonlats:
        return []
    lons = [p[0] for p in lonlats]
    lats = [p[1] for p in lonlats]
    bbox = BBox(min(lons), min(lats), max(lons), max(lats))
    return tiles_para_bbox(bbox, tamanho_km, buffer_m)


def unir_tiles(*listas: list[Tile]) -> list[Tile]:
    """Une listas de tiles removendo duplicados (mantem determinismo por id)."""
    vistos: dict[str, Tile] = {}
    for lista in listas:
        for t in lista:
            vistos.setdefault(t.id, t)
    return [vistos[k] for k in sorted(vistos)]
