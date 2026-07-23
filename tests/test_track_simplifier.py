"""Testes de limpeza e simplificacao do trajeto."""
from __future__ import annotations

import datetime as _dt

from src import gpx_reader, track_cleaner, track_simplifier, utils
from src.models import TrackPoint

from conftest import lonlat


def _reta(n=200, passo_m=1.0):
    """Reta oeste-leste com pontos a cada passo_m."""
    t0 = _dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
    pts = []
    for i in range(n):
        lon, lat = lonlat(i * passo_m, 0.0)
        pts.append(TrackPoint(indice=i, lat=lat, lon=lon,
                              tempo=t0 + _dt.timedelta(seconds=i)))
    gpx_reader.recomputar_metricas(pts)
    return pts


def test_simplificacao_reduz_e_preserva_extremos(cfg):
    pts = _reta(300, 1.0)
    simpl, ss = track_simplifier.simplificar(pts, cfg)
    ss.n_original = len(pts)
    assert ss.n_apos_simplificacao < len(pts)
    assert ss.reducao_pct > 50
    # primeiro e ultimo preservados
    assert (simpl[0].lat, simpl[0].lon) == (pts[0].lat, pts[0].lon)
    assert (simpl[-1].lat, simpl[-1].lon) == (pts[-1].lat, pts[-1].lon)


def test_preserva_curva(cfg):
    """Uma curva de 90 graus deve manter um ponto proximo ao vertice."""
    t0 = _dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
    pts = []
    caminho = [(x, 0.0) for x in range(0, 100, 2)] + [(100, y) for y in range(0, 100, 2)]
    for i, (e, n) in enumerate(caminho):
        lon, lat = lonlat(e, n)
        pts.append(TrackPoint(indice=i, lat=lat, lon=lon,
                              tempo=t0 + _dt.timedelta(seconds=i)))
    gpx_reader.recomputar_metricas(pts)
    simpl, _ = track_simplifier.simplificar(pts, cfg)
    # deve existir um ponto de amostra proximo ao vertice (100,0)
    vlon, vlat = lonlat(100, 0)
    perto = min(utils.haversine_m(vlat, vlon, s.lat, s.lon) for s in simpl)
    assert perto < 5.0


def test_limpeza_remove_duplicados_exatos(cfg):
    pts = _reta(50, 2.0)
    # inserir duplicado exato
    dup = TrackPoint(indice=999, lat=pts[10].lat, lon=pts[10].lon,
                     tempo=pts[10].tempo + _dt.timedelta(milliseconds=10))
    pts.insert(11, dup)
    gpx_reader.recomputar_metricas(pts)
    limpos, stats = track_cleaner.limpar(pts, cfg)
    assert stats.removidos_duplicados >= 1
    assert len(limpos) < len(pts)


def test_limpeza_remove_spike(cfg):
    """Um ponto que salta para longe e volta deve ser removido como spike."""
    pts = _reta(40, 3.0)
    # spike lateral enorme no meio
    lon, lat = lonlat(20 * 3.0, 5000.0)  # 5 km ao norte, instantaneo
    pts[20].lat, pts[20].lon = lat, lon
    gpx_reader.recomputar_metricas(pts)
    limpos, stats = track_cleaner.limpar(pts, cfg)
    assert stats.removidos_salto >= 1


def test_limpeza_afina_parado(cfg):
    """Muitos pontos parados no mesmo lugar devem ser afinados."""
    t0 = _dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
    pts = _reta(20, 3.0)
    base = pts[-1]
    for k in range(20):  # 20 pontos praticamente no mesmo lugar
        lon, lat = lonlat(19 * 3.0 + 0.02 * k, 0.0)
        pts.append(TrackPoint(indice=100 + k, lat=lat, lon=lon,
                              tempo=base.tempo + _dt.timedelta(seconds=1 + k)))
    gpx_reader.recomputar_metricas(pts)
    limpos, stats = track_cleaner.limpar(pts, cfg)
    assert stats.removidos_parado > 0
