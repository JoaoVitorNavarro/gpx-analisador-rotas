"""Leitura e inspecao de arquivos GPX (usando gpxpy).

Suporta multiplas trilhas e segmentos, preserva a ordem cronologica, extrai
lat/lon/altitude/tempo e campos auxiliares (fix, hdop), ignora coordenadas
invalidas e calcula metricas derivadas (distancia, distancia acumulada,
velocidade estimada e azimute do deslocamento).

Nao presume que todos os GPX tenham a mesma estrutura: campos ausentes viram
None e sao registrados; nunca lanca excecao por um campo opcional faltando.
"""
from __future__ import annotations

from pathlib import Path

import gpxpy

from . import utils
from .models import GpxInfo, TrackPoint

logger = utils.get_logger()


def _extrair_hdop_fix(pt) -> tuple[float | None, str | None]:
    """Obtem hdop e fix de um GPXTrackPoint, tolerando variacoes de esquema."""
    hdop = getattr(pt, "horizontal_dilution", None)
    fix = getattr(pt, "type_of_gpx_fix", None)
    # fallback: varrer extensoes cruas caso a lib nao tenha mapeado
    if hdop is None or fix is None:
        for ext in (getattr(pt, "extensions", None) or []):
            try:
                tag = ext.tag.split("}")[-1].lower()
                if tag == "hdop" and hdop is None and ext.text:
                    hdop = float(ext.text)
                elif tag == "fix" and fix is None and ext.text:
                    fix = ext.text
            except (AttributeError, ValueError):
                continue
    try:
        hdop = float(hdop) if hdop is not None else None
    except (TypeError, ValueError):
        hdop = None
    return hdop, fix


def ler_gpx(caminho: str | Path) -> tuple[list[TrackPoint], GpxInfo]:
    """Le um GPX e retorna (pontos_ordenados, info).

    As metricas derivadas (dist_prev, dist_acum, velocidade, azimute) sao
    calculadas em relacao ao ponto valido imediatamente anterior.
    """
    caminho = Path(caminho)
    info = GpxInfo(caminho=str(caminho), nome=caminho.name)

    with open(caminho, "r", encoding="utf-8", errors="replace") as f:
        gpx = gpxpy.parse(f)

    campos: set[str] = set()
    pontos: list[TrackPoint] = []
    n_trilhas = 0
    n_segmentos = 0
    n_brutos = 0
    n_invalidos = 0

    for it, trilha in enumerate(gpx.tracks):
        n_trilhas += 1
        for iseg, seg in enumerate(trilha.segments):
            n_segmentos += 1
            for pt in seg.points:
                n_brutos += 1
                lat, lon = pt.latitude, pt.longitude
                if not utils.coordenada_valida(lat, lon):
                    n_invalidos += 1
                    continue
                hdop, fix = _extrair_hdop_fix(pt)
                if pt.elevation is not None:
                    campos.add("ele")
                if pt.time is not None:
                    campos.add("time")
                if hdop is not None:
                    campos.add("hdop")
                if fix is not None:
                    campos.add("fix")
                pontos.append(TrackPoint(
                    indice=len(pontos), lat=float(lat), lon=float(lon),
                    ele=(float(pt.elevation) if pt.elevation is not None else None),
                    tempo=pt.time, fix=fix, hdop=hdop,
                    trilha=it, segmento=iseg,
                ))

    # metricas derivadas
    _calcular_metricas(pontos, info)

    info.n_trilhas = n_trilhas
    info.n_segmentos = n_segmentos
    info.n_pontos = len(pontos)
    info.campos = sorted(campos)
    info.tem_tempo = any(p.tempo is not None for p in pontos)

    if n_invalidos:
        info.anomalias.append(f"{n_invalidos} coordenada(s) invalida(s) ignorada(s)")
    if not pontos:
        info.anomalias.append("Nenhum ponto valido encontrado")

    logger.info("GPX %s: %d trilha(s), %d segmento(s), %d ponto(s) validos (%d brutos)",
                caminho.name, n_trilhas, n_segmentos, len(pontos), n_brutos)
    return pontos, info


def _calcular_metricas(pontos: list[TrackPoint], info: GpxInfo) -> None:
    """Preenche dist_prev, dist_acum, velocidade e azimute; agrega bbox/tempo."""
    if not pontos:
        return
    lat_min = lat_max = pontos[0].lat
    lon_min = lon_max = pontos[0].lon
    acum = 0.0
    tempos = []
    saltos = 0

    anterior: TrackPoint | None = None
    for p in pontos:
        lat_min, lat_max = min(lat_min, p.lat), max(lat_max, p.lat)
        lon_min, lon_max = min(lon_min, p.lon), max(lon_max, p.lon)
        if p.tempo is not None:
            tempos.append(p.tempo)
        if anterior is not None:
            d = utils.haversine_m(anterior.lat, anterior.lon, p.lat, p.lon)
            p.dist_prev_m = d
            acum += d
            p.dist_acum_m = acum
            p.azimute = utils.azimute_graus(anterior.lat, anterior.lon, p.lat, p.lon)
            if anterior.tempo is not None and p.tempo is not None:
                dt = (p.tempo - anterior.tempo).total_seconds()
                if dt > 0:
                    p.vel_kmh = (d / dt) * 3.6
                    if p.vel_kmh > 400:  # salto claramente impossivel
                        saltos += 1
        else:
            p.dist_prev_m = 0.0
            p.dist_acum_m = 0.0
        anterior = p

    # herdar azimute do ponto seguinte para o primeiro ponto
    if len(pontos) > 1 and pontos[0].azimute is None:
        pontos[0].azimute = pontos[1].azimute

    info.distancia_total_m = acum
    info.lat_min, info.lat_max = lat_min, lat_max
    info.lon_min, info.lon_max = lon_min, lon_max
    if tempos:
        info.tempo_inicial = min(tempos)
        info.tempo_final = max(tempos)
        info.duracao_s = (info.tempo_final - info.tempo_inicial).total_seconds()
    if saltos:
        info.anomalias.append(f"{saltos} salto(s) de velocidade > 400 km/h detectado(s)")

    # pontos duplicados exatos (consecutivos)
    dups = sum(1 for i in range(1, len(pontos))
               if pontos[i].lat == pontos[i - 1].lat and pontos[i].lon == pontos[i - 1].lon)
    if dups:
        info.anomalias.append(f"{dups} ponto(s) duplicado(s) consecutivo(s)")


def recomputar_metricas(pontos: list[TrackPoint]) -> None:
    """Reindexa e recalcula metricas por ponto apos limpeza/simplificacao.

    Nao altera bbox/tempo do GpxInfo (que refletem o arquivo original).
    """
    anterior: TrackPoint | None = None
    acum = 0.0
    for i, p in enumerate(pontos):
        p.indice = i
        if anterior is None:
            p.dist_prev_m = 0.0
            p.dist_acum_m = 0.0
            p.azimute = None
            p.vel_kmh = None
        else:
            d = utils.haversine_m(anterior.lat, anterior.lon, p.lat, p.lon)
            p.dist_prev_m = d
            acum += d
            p.dist_acum_m = acum
            p.azimute = utils.azimute_graus(anterior.lat, anterior.lon, p.lat, p.lon)
            if anterior.tempo is not None and p.tempo is not None:
                dt = (p.tempo - anterior.tempo).total_seconds()
                p.vel_kmh = (d / dt) * 3.6 if dt > 0 else None
            else:
                p.vel_kmh = None
        anterior = p
    if len(pontos) > 1 and pontos[0].azimute is None:
        pontos[0].azimute = pontos[1].azimute


def inspecionar(caminho: str | Path) -> GpxInfo:
    """Inspeciona um GPX e retorna apenas os metadados (sem manter os pontos)."""
    _, info = ler_gpx(caminho)
    return info


def encontrar_gpx(entrada: str | Path, recursivo: bool = True) -> list[Path]:
    """Lista arquivos .gpx a partir de um arquivo ou pasta (opcional recursivo)."""
    p = Path(entrada)
    if p.is_file():
        return [p] if p.suffix.lower() == ".gpx" else []
    if p.is_dir():
        it = p.rglob("*.gpx") if recursivo else p.glob("*.gpx")
        return sorted(x for x in it if x.is_file())
    return []
