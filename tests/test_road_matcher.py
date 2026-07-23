"""Testes do map matching (HMM/Viterbi) com malha viaria sintetica:
ruas paralelas, cruzamento, ruido lateral e via sem nome."""
from __future__ import annotations

from src import road_matcher
from src.road_matcher import identidade_via

from conftest import grafo_vias, pontos_ao_longo


def _rids(matched):
    return [mp.escolhido.road_id if mp.escolhido else None for mp in matched]


def _nomes_em_ordem(matched):
    seq = []
    for mp in matched:
        nome = mp.escolhido.nome if mp.escolhido else None
        if nome and (not seq or seq[-1] != nome):
            seq.append(nome)
    return seq


def test_ruas_paralelas_sem_flipflop(cfg):
    """Duas avenidas paralelas a 18 m; o veiculo anda sobre a de baixo.
    Nao deve alternar entre elas por ruido."""
    roads = [
        {"name": "Avenida Sul", "highway": "primary",
         "nodes": [(0, 0), (100, 0), (200, 0)]},
        {"name": "Avenida Norte", "highway": "primary",
         "nodes": [(0, 18), (100, 18), (200, 18)]},
    ]
    gv = grafo_vias(roads)
    pts = pontos_ao_longo([(0, 1), (200, 1)], passo_m=5, ruido_lat_m=2.0)
    matched = road_matcher.map_match(pts, gv, cfg)
    rids = [r for r in _rids(matched) if r]
    assert len(set(rids)) == 1, f"esperava 1 via, veio {set(rids)}"
    assert "Avenida Sul" in matched[0].escolhido.nome


def test_cruzamento_sequencia(cfg):
    """Vai a leste na Rua A e vira ao norte na Rua B: sequencia [A, B]."""
    roads = [
        {"name": "Rua A", "highway": "residential",
         "nodes": [(-100, 0), (0, 0), (100, 0)]},
        {"name": "Rua B", "highway": "residential",
         "nodes": [(0, -100), (0, 0), (0, 100)]},
    ]
    gv = grafo_vias(roads)
    pts = pontos_ao_longo([(-90, 0), (0, 0), (0, 90)], passo_m=5, ruido_lat_m=1.0)
    matched = road_matcher.map_match(pts, gv, cfg)
    nomes = _nomes_em_ordem(matched)
    assert nomes == ["Rua A", "Rua B"], f"sequencia inesperada: {nomes}"


def test_ruido_lateral_mantem_via(cfg):
    """Com ruido lateral de ate 6 m, deve permanecer na mesma rua."""
    roads = [{"name": "Estrada Unica", "highway": "tertiary",
              "nodes": [(0, 0), (150, 0), (300, 0)]}]
    gv = grafo_vias(roads)
    pts = pontos_ao_longo([(0, 0), (300, 0)], passo_m=4, ruido_lat_m=6.0)
    matched = road_matcher.map_match(pts, gv, cfg)
    rids = [r for r in _rids(matched) if r]
    assert len(rids) >= len(matched) - 1  # quase todos identificados
    assert len(set(rids)) == 1


def test_identidade_via_sem_nome():
    rid, nome, ref, hw = identidade_via({"highway": "service", "osmid": 42})
    assert "Via sem nome" in nome
    assert "42" in nome
    assert rid.startswith("osm:")


def test_identidade_prioriza_name_sobre_ref():
    rid, nome, ref, hw = identidade_via(
        {"name": "Avenida das Torres", "ref": "AM-010", "highway": "primary"})
    assert nome == "Avenida das Torres"
    assert ref == "AM-010"
    assert rid.startswith("name:")


def test_identidade_ref_quando_sem_nome():
    rid, nome, ref, hw = identidade_via({"ref": "BR-319", "highway": "trunk"})
    assert nome == "BR-319"
    assert rid.startswith("ref:")
