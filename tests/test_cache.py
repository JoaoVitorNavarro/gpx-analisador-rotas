"""Testes de zona UTM, criacao/determinismo de tiles e banco de cache."""
from __future__ import annotations

from src import osm_cache
from src.coordinate_system import BBox, utm_epsg
from src.database import Database


def test_utm_epsg_manaus():
    # Manaus (-3.02, -59.94) -> UTM 21 Sul
    assert utm_epsg(-59.94, -3.02) == 32721


def test_utm_epsg_hemisferio_norte():
    assert utm_epsg(-3.7, 40.4) // 100 == 326  # Madri, hemisferio norte


def test_tile_determinismo():
    t1 = osm_cache.tile_de(-59.94, -3.02, 5)
    t2 = osm_cache.tile_de(-59.941, -3.021, 5)  # ponto proximo -> mesmo tile
    assert t1.id == t2.id


def test_tile_bounds_contem_ponto():
    t = osm_cache.tile_de(-59.94, -3.02, 5)
    lo0, la0, lo1, la1 = t.bounds
    assert lo0 <= -59.94 <= lo1
    assert la0 <= -3.02 <= la1


def test_tiles_para_pontos_um_tile():
    pontos = [(-59.9379, -3.0267), (-59.9428, -3.0253)]
    tiles = osm_cache.tiles_para_pontos(pontos, 5, buffer_m=300)
    ids = {t.id for t in tiles}
    assert len(ids) == 1  # rota pequena cabe em 1 tile de 5 km


def test_uniao_tiles_sem_duplicar():
    a = osm_cache.tiles_para_bbox(BBox(-59.96, -3.06, -59.92, -3.02), 5)
    b = osm_cache.tiles_para_bbox(BBox(-59.93, -3.03, -59.90, -3.00), 5)
    uni = osm_cache.unir_tiles(a, b)
    assert len(uni) == len({t.id for t in uni})


def test_banco_registra_e_valida_tile(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    t = osm_cache.tile_de(-59.94, -3.02, 5)
    db.registrar_tile(t.id, t.bounds, 5, 100, "x.graphml", "ok", versao_base="2026-07-01")
    assert db.tile_valido(t.id, validade_dias=90) is True
    assert db.tile_valido(t.id, validade_dias=-1) is False  # forca "vencido" (idade 0 > -1)
    row = db.obter_tile(t.id)
    assert row["n_vias"] == 100 and row["situacao"] == "ok"
    n = db.limpar_tiles()
    assert n == 1
    assert db.obter_tile(t.id) is None
    db.fechar()


def test_banco_duplicidade_arquivo(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    db.registrar_arquivo(caminho="a.gpx", nome="a.gpx", hash_arquivo="H1",
                         config_hash="C1", config={}, status="ok", n_pontos=10,
                         n_trechos=2, distancia_m=100.0, resumo={}, versao_programa="1.0")
    assert db.buscar_resultado("H1", "C1") is not None
    assert db.buscar_resultado("H1", "C2") is None
    db.fechar()
