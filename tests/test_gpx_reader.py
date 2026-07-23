"""Testes de leitura de GPX: campos, distancia, duplicados, sem horario,
multiplos segmentos, arquivo vazio e arquivo corrompido."""
from __future__ import annotations

import textwrap

import pytest

from src import gpx_reader, utils

GPX_2SEG = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="teste"><trk><name>t</name>
<trkseg>
<trkpt lat="-3.020000" lon="-59.940000"><ele>50</ele><time>2026-07-01T12:00:00Z</time><hdop>1.2</hdop><fix>3d</fix></trkpt>
<trkpt lat="-3.020000" lon="-59.940000"><ele>50</ele><time>2026-07-01T12:00:01Z</time><hdop>1.2</hdop></trkpt>
<trkpt lat="-3.019100" lon="-59.940000"><ele>51</ele><time>2026-07-01T12:00:02Z</time></trkpt>
</trkseg>
<trkseg>
<trkpt lat="-3.019000" lon="-59.940000"><time>2026-07-01T12:00:05Z</time></trkpt>
<trkpt lat="-3.018000" lon="-59.940000"><time>2026-07-01T12:00:06Z</time></trkpt>
</trkseg>
</trk></gpx>
""")

GPX_SEM_TEMPO = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="teste"><trk><trkseg>
<trkpt lat="-3.02" lon="-59.94"></trkpt>
<trkpt lat="-3.019" lon="-59.94"></trkpt>
</trkseg></trk></gpx>
""")

GPX_VAZIO = '<?xml version="1.0"?><gpx version="1.1" creator="t"><trk><trkseg></trkseg></trk></gpx>'
GPX_CORROMPIDO = "isto nao e um gpx <<< >>>"


def _escrever(tmp_path, nome, conteudo):
    p = tmp_path / nome
    p.write_text(conteudo, encoding="utf-8")
    return p


def test_le_dois_segmentos_e_campos(tmp_path):
    p = _escrever(tmp_path, "a.gpx", GPX_2SEG)
    pts, info = gpx_reader.ler_gpx(p)
    assert info.n_trilhas == 1
    assert info.n_segmentos == 2
    assert info.n_pontos == 5
    assert set(["ele", "time", "hdop", "fix"]).issubset(set(info.campos))
    assert pts[0].hdop == 1.2 and pts[0].fix == "3d"
    assert info.tem_tempo is True


def test_distancia_e_acumulada(tmp_path):
    p = _escrever(tmp_path, "a.gpx", GPX_2SEG)
    pts, info = gpx_reader.ler_gpx(p)
    # 0.0009 graus de latitude ~ 100 m
    esperado = utils.haversine_m(-3.020000, -59.94, -3.0191, -59.94)
    assert pts[2].dist_prev_m == pytest.approx(esperado, rel=0.01)
    assert pts[-1].dist_acum_m > pts[0].dist_acum_m
    assert info.distancia_total_m == pytest.approx(pts[-1].dist_acum_m, rel=1e-6)


def test_detecta_duplicado_consecutivo(tmp_path):
    p = _escrever(tmp_path, "a.gpx", GPX_2SEG)
    _, info = gpx_reader.ler_gpx(p)
    assert any("duplicado" in a for a in info.anomalias)


def test_sem_tempo(tmp_path):
    p = _escrever(tmp_path, "s.gpx", GPX_SEM_TEMPO)
    pts, info = gpx_reader.ler_gpx(p)
    assert info.tem_tempo is False
    assert all(pt.vel_kmh is None for pt in pts)
    assert info.duracao_s is None


def test_gpx_vazio(tmp_path):
    p = _escrever(tmp_path, "v.gpx", GPX_VAZIO)
    pts, info = gpx_reader.ler_gpx(p)
    assert pts == []
    assert info.n_pontos == 0
    assert any("Nenhum ponto" in a for a in info.anomalias)


def test_gpx_corrompido(tmp_path):
    p = _escrever(tmp_path, "c.gpx", GPX_CORROMPIDO)
    with pytest.raises(Exception):
        gpx_reader.ler_gpx(p)


def test_encontrar_gpx_recursivo(tmp_path):
    (tmp_path / "sub").mkdir()
    _escrever(tmp_path, "a.gpx", GPX_2SEG)
    _escrever(tmp_path / "sub", "b.gpx", GPX_2SEG)
    achados = gpx_reader.encontrar_gpx(tmp_path, recursivo=True)
    assert len(achados) == 2
    achados_n = gpx_reader.encontrar_gpx(tmp_path, recursivo=False)
    assert len(achados_n) == 1
