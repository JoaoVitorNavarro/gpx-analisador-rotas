"""Testes de agrupamento de vias, remocao de oscilacao, retorno a mesma via,
classificacao de confianca e cancelamento seguro."""
from __future__ import annotations

import datetime as _dt

from src import confidence, route_processor
from src.database import Database
from src.models import (CONF_ALTA, CONF_BAIXA, CONF_NAO_IDENT, Candidato,
                        MatchedPoint, TrackPoint, Trecho)
from src.route_processor import BatchProcessor, Reporter

LAT0, LON0 = -3.02, -59.94


def _matched(rids, dist_prev=5.0, nomes=None):
    nomes = nomes or {}
    t0 = _dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
    matched = []
    simpl = []
    for i, rid in enumerate(rids):
        tp = TrackPoint(indice=i, lat=LAT0 + i * 1e-5, lon=LON0,
                        tempo=t0 + _dt.timedelta(seconds=i), azimute=90.0)
        tp.dist_prev_m = 0.0 if i == 0 else dist_prev
        simpl.append(tp)
        cand = None
        if rid is not None:
            cand = Candidato(edge_key=(i, i + 1, 0), road_id=rid,
                             nome=nomes.get(rid, rid), highway="residential",
                             dist_perp_m=3.0, azimute_via=90.0,
                             proj_lon=tp.lon, proj_lat=tp.lat)
        matched.append(MatchedPoint(indice=i, tp=tp, escolhido=cand,
                                    dist_via_m=(3.0 if cand else None),
                                    margem_score=5.0))
    return matched, simpl


def test_agrupa_consecutivos(cfg):
    # trechos com distancia acima da permanencia minima (20 m): 6 pts x 10 m
    matched, simpl = _matched(["A"] * 6 + ["B"] * 6, dist_prev=10.0)
    trechos = route_processor.agrupar_trechos(matched, simpl, cfg)
    assert [t.road_id for t in trechos] == ["A", "B"]


def test_remove_oscilacao_curta(cfg):
    # A...(long) b(1 ponto, 5m) A...(long) -> b some, vira A unico
    rids = ["A"] * 10 + ["B"] + ["A"] * 10
    matched, simpl = _matched(rids, dist_prev=5.0)
    trechos = route_processor.agrupar_trechos(matched, simpl, cfg)
    assert [t.road_id for t in trechos] == ["A"], \
        f"oscilacao nao removida: {[t.road_id for t in trechos]}"


def test_preserva_retorno_real(cfg):
    # A(50m) B(50m) A(50m), todos acima da permanencia minima (20m) -> preserva
    rids = ["A"] * 10 + ["B"] * 10 + ["A"] * 10
    matched, simpl = _matched(rids, dist_prev=5.0)
    trechos = route_processor.agrupar_trechos(matched, simpl, cfg)
    assert [t.road_id for t in trechos] == ["A", "B", "A"], \
        f"retorno real nao preservado: {[t.road_id for t in trechos]}"


def test_trecho_nao_identificado(cfg):
    rids = ["A"] * 5 + [None] * 5 + ["A"] * 5
    matched, simpl = _matched(rids, dist_prev=6.0)
    trechos = route_processor.agrupar_trechos(matched, simpl, cfg)
    niveis = [t.confianca for t in trechos]
    assert CONF_NAO_IDENT in niveis


def test_confianca_alta_e_baixa(cfg):
    bom = Trecho(road_id="A", nome_via="A", n_pontos=20, dist_media_m=3.0, dist_max_m=6.0)
    assert confidence.classificar(bom, azdiff_medio=5.0, margem_media=5.0, cfg=cfg) == CONF_ALTA

    ruim = Trecho(road_id="A", nome_via="A", n_pontos=20, dist_media_m=40.0, dist_max_m=60.0)
    assert confidence.classificar(ruim, azdiff_medio=80.0, margem_media=0.1, cfg=cfg) == CONF_BAIXA

    ni = Trecho(road_id="", nome_via="Nao identificado")
    assert confidence.classificar(ni, None, None, cfg) == CONF_NAO_IDENT


def test_cancelamento_antes_de_iniciar(cfg, tmp_path):
    """Se o cancelamento ja estiver ativo, o lote retorna sem processar."""
    db = Database(tmp_path / "c.sqlite")
    rep = Reporter(cancelado=lambda: True)
    bp = BatchProcessor(cfg, db, rep)
    resultados = bp.processar(["qualquer.gpx"], tmp_path / "out")
    db.fechar()
    assert resultados == []
