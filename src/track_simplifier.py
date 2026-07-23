"""Simplificacao inteligente do trajeto (o original permanece intacto).

Combina tres criterios, conforme especificado:
  1. Amostragem por DISTANCIA FISICA (nao "1 a cada N registros"): garante um
     espacamento maximo aproximado (padrao 10 m) mesmo em retas.
  2. Douglas-Peucker (em coordenadas metricas): remove vertices colineares
     redundantes preservando o formato geral e as curvas.
  3. Preservacao de mudancas de direcao relevantes (curvas), de mudancas
     bruscas de velocidade e, obrigatoriamente, do primeiro e do ultimo ponto.

O conjunto final e a UNIAO dos pontos selecionados por cada criterio; assim
nenhuma curva/cruzamento e perdido e as retas ficam com espacamento regular.
Todos os pontos mantidos sao pontos ORIGINAIS (preservam tempo/altitude/hdop),
o que e essencial para calcular horarios de entrada/saida por via.
"""
from __future__ import annotations

from . import gpx_reader, utils
from .config_loader import Config
from .coordinate_system import CoordinateSystem
from .models import SimplifyStats, TrackPoint

logger = utils.get_logger()


def simplificar(pontos: list[TrackPoint], cfg: Config,
                cs: CoordinateSystem | None = None) -> tuple[list[TrackPoint], SimplifyStats]:
    """Simplifica preservando pontos originais. Retorna (pontos, stats)."""
    stats = SimplifyStats(n_apos_limpeza=len(pontos))
    if len(pontos) <= 2:
        stats.n_apos_simplificacao = len(pontos)
        return list(pontos), stats

    if cs is None:
        cs = CoordinateSystem.para_pontos([p.as_lonlat() for p in pontos])
    xy = cs.pontos_to_m([p.as_lonlat() for p in pontos])

    espac = float(cfg.get("espacamento_pontos_m", 10.0))
    tol_dp = float(cfg.get("tolerancia_douglas_peucker_m", 3.0))
    ang_curva = float(cfg.get("angulo_preservacao_curva_graus", 20.0))

    manter: set[int] = {0, len(pontos) - 1}
    manter |= _anchors_curva_velocidade(pontos, ang_curva)
    manter |= _douglas_peucker(xy, tol_dp)
    manter |= _amostragem_distancia(xy, espac)
    manter = _thin_muito_proximos(xy, manter, espac,
                                  protegidos=_anchors_curva_velocidade(pontos, ang_curva)
                                  | {0, len(pontos) - 1})

    idx = sorted(manter)
    simpl = [_clonar(pontos[i]) for i in idx]
    gpx_reader.recomputar_metricas(simpl)

    stats.n_apos_simplificacao = len(simpl)
    logger.info("Simplificacao: %d -> %d pontos (espac=%.0fm, dp=%.0fm) reducao %.1f%%",
                len(pontos), len(simpl), espac, tol_dp, stats.reducao_pct)
    return simpl, stats


def _clonar(p: TrackPoint) -> TrackPoint:
    return TrackPoint(
        indice=p.indice, lat=p.lat, lon=p.lon, ele=p.ele, tempo=p.tempo,
        fix=p.fix, hdop=p.hdop, trilha=p.trilha, segmento=p.segmento,
    )


def _anchors_curva_velocidade(pontos: list[TrackPoint], ang_curva: float) -> set[int]:
    """Marca pontos de curva (mudanca de azimute > limiar) e de mudanca brusca
    de velocidade."""
    anchors: set[int] = set()
    for i in range(1, len(pontos) - 1):
        a1 = pontos[i].azimute
        a2 = pontos[i + 1].azimute
        if a1 is not None and a2 is not None:
            if utils.diff_angular(a1, a2) >= ang_curva:
                anchors.add(i)
        v0 = pontos[i - 1].vel_kmh
        v1 = pontos[i].vel_kmh
        if v0 is not None and v1 is not None and abs(v1 - v0) >= 15.0:
            anchors.add(i)
    return anchors


def _amostragem_distancia(xy: list[tuple[float, float]], espac: float) -> set[int]:
    """Seleciona pontos de modo que o espacamento acumulado nao passe de 'espac'."""
    manter = {0}
    acum = 0.0
    for i in range(1, len(xy)):
        dx = xy[i][0] - xy[i - 1][0]
        dy = xy[i][1] - xy[i - 1][1]
        acum += (dx * dx + dy * dy) ** 0.5
        if acum >= espac:
            manter.add(i)
            acum = 0.0
    manter.add(len(xy) - 1)
    return manter


def _douglas_peucker(xy: list[tuple[float, float]], tol: float) -> set[int]:
    """Douglas-Peucker iterativo (sem recursao). Retorna indices preservados."""
    n = len(xy)
    if n <= 2:
        return set(range(n))
    manter = [False] * n
    manter[0] = manter[n - 1] = True
    pilha = [(0, n - 1)]
    while pilha:
        ini, fim = pilha.pop()
        dmax = 0.0
        idx = -1
        ax, ay = xy[ini]
        bx, by = xy[fim]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        for i in range(ini + 1, fim):
            px, py = xy[i]
            if seg2 == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = max(0.0, min(1.0, t))
                projx, projy = ax + t * dx, ay + t * dy
                d = ((px - projx) ** 2 + (py - projy) ** 2) ** 0.5
            if d > dmax:
                dmax = d
                idx = i
        if dmax > tol and idx != -1:
            manter[idx] = True
            pilha.append((ini, idx))
            pilha.append((idx, fim))
    return {i for i, m in enumerate(manter) if m}


def _thin_muito_proximos(xy, manter: set[int], espac: float,
                         protegidos: set[int]) -> set[int]:
    """Remove pontos mantidos que ficaram muito proximos entre si (< 30% do
    espacamento), exceto os protegidos (curvas/extremos)."""
    limite = max(1.0, espac * 0.3)
    idx = sorted(manter)
    resultado = []
    ultimo_xy = None
    for i in idx:
        if i in protegidos or ultimo_xy is None:
            resultado.append(i)
            ultimo_xy = xy[i]
            continue
        d = ((xy[i][0] - ultimo_xy[0]) ** 2 + (xy[i][1] - ultimo_xy[1]) ** 2) ** 0.5
        if d >= limite:
            resultado.append(i)
            ultimo_xy = xy[i]
    return set(resultado)
