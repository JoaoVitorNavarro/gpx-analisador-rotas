"""Sistema de coordenadas: deteccao automatica de zona UTM e transformacoes.

Os GPX vem em WGS84 (graus). Para calcular distancias em metros de forma
confiavel, projetamos temporariamente o trajeto e as vias para UTM (metros),
fazemos as contas, e voltamos para WGS84 apenas quando necessario (mapas).

Estrategia para percursos que cruzam mais de uma zona UTM:
  A zona e escolhida pelo CENTROIDE do percurso. Para as extensoes tipicas de
  um video de GoPro (poucos km), a distorcao de escala do UTM fora da zona
  central e desprezivel (< 0,1% ate ~200 km do meridiano central). Quando o
  percurso realmente cruza a fronteira de zonas, registramos um aviso e
  mantemos a zona do centroide (decisao documentada em DECISOES_TECNICAS.md).
  Alternativa disponivel: projecao azimutal equidistante local (metodo
  `transformer_local_aeqd`), util caso um percurso futuro seja muito extenso.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from pyproj import CRS, Transformer

from . import utils

logger = utils.get_logger()

WGS84 = "EPSG:4326"


def utm_epsg(lon: float, lat: float) -> int:
    """Retorna o codigo EPSG da zona UTM para (lon, lat)."""
    zona = int((lon + 180.0) // 6.0) + 1
    zona = max(1, min(60, zona))
    if lat >= 0:
        return 32600 + zona   # hemisferio norte
    return 32700 + zona       # hemisferio sul


@dataclass
class BBox:
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

    @property
    def centro(self) -> tuple[float, float]:
        return ((self.lon_min + self.lon_max) / 2.0,
                (self.lat_min + self.lat_max) / 2.0)


class CoordinateSystem:
    """Encapsula transformacoes WGS84 <-> metrico para uma regiao.

    Instancie a partir do bounding box ou centroide do percurso; a partir dai
    todas as conversoes usam a mesma projecao, garantindo consistencia.
    """

    def __init__(self, epsg_metrico: int, descricao: str = ""):
        self.epsg_metrico = epsg_metrico
        self.crs_metrico = CRS.from_user_input(epsg_metrico) if isinstance(epsg_metrico, int) \
            else CRS.from_user_input(epsg_metrico)
        self.descricao = descricao or f"EPSG:{epsg_metrico}"
        self._to_m = Transformer.from_crs(WGS84, self.crs_metrico, always_xy=True)
        self._to_g = Transformer.from_crs(self.crs_metrico, WGS84, always_xy=True)

    # ------- classe factory -------
    @classmethod
    def para_bbox(cls, bbox: BBox) -> "CoordinateSystem":
        lon_c, lat_c = bbox.centro
        epsg = utm_epsg(lon_c, lat_c)
        z1 = int((bbox.lon_min + 180) // 6)
        z2 = int((bbox.lon_max + 180) // 6)
        if z1 != z2:
            logger.warning(
                "Percurso cruza fronteira de zona UTM (lon %.4f..%.4f); "
                "usando zona do centroide EPSG:%d (distorcao desprezivel).",
                bbox.lon_min, bbox.lon_max, epsg,
            )
        return cls(epsg, descricao=f"UTM EPSG:{epsg}")

    @classmethod
    def para_pontos(cls, lonlats: Sequence[tuple[float, float]]) -> "CoordinateSystem":
        lons = [p[0] for p in lonlats]
        lats = [p[1] for p in lonlats]
        return cls.para_bbox(BBox(min(lons), min(lats), max(lons), max(lats)))

    # ------- transformacoes de pontos -------
    def to_m(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = self._to_m.transform(lon, lat)
        return (x, y)

    def to_wgs(self, x: float, y: float) -> tuple[float, float]:
        lon, lat = self._to_g.transform(x, y)
        return (lon, lat)

    def pontos_to_m(self, lonlats: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        lons = [p[0] for p in lonlats]
        lats = [p[1] for p in lonlats]
        xs, ys = self._to_m.transform(lons, lats)
        return list(zip(xs, ys))

    def pontos_to_wgs(self, xys: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        xs = [p[0] for p in xys]
        ys = [p[1] for p in xys]
        lons, lats = self._to_g.transform(xs, ys)
        return list(zip(lons, lats))

    # ------- funcoes cruas p/ shapely.ops.transform -------
    @property
    def fn_to_m(self) -> Callable:
        return self._to_m.transform

    @property
    def fn_to_wgs(self) -> Callable:
        return self._to_g.transform


def transformer_local_aeqd(lon0: float, lat0: float) -> CoordinateSystem:
    """Projecao azimutal equidistante centrada em (lon0, lat0).

    Alternativa a UTM para percursos muito extensos ou que cruzem varias zonas:
    preserva distancias a partir do centro. Documentada como plano B.
    """
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +x_0=0 +y_0=0 "
        f"+datum=WGS84 +units=m +no_defs"
    )
    cs = CoordinateSystem.__new__(CoordinateSystem)
    cs.epsg_metrico = -1
    cs.crs_metrico = crs
    cs.descricao = f"AEQD lon0={lon0:.4f} lat0={lat0:.4f}"
    cs._to_m = Transformer.from_crs(WGS84, crs, always_xy=True)
    cs._to_g = Transformer.from_crs(crs, WGS84, always_xy=True)
    return cs
