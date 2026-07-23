"""Orquestracao do processamento: por arquivo e em lote.

Fluxo por arquivo:
  ler GPX -> limpar -> simplificar -> (grafo do OSM ja garantido) -> map matching
  -> agrupar em trechos (suavizando oscilacoes) -> estatisticas -> confianca.

Fluxo em lote (BatchProcessor):
  1. Prepara todos os arquivos (leitura/limpeza/simplificacao) e acumula as
     areas necessarias (tiles).
  2. Garante os tiles UMA vez (baixa apenas os ausentes; reaproveita o resto).
  3. Para cada arquivo: obtem o grafo (cache de grafos por conjunto de tiles),
     roda o matching, agrupa, calcula, gera as saidas e registra no banco.
  Erros em um arquivo nao interrompem o lote; sao registrados e o proximo segue.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import (config_loader, confidence, gpx_reader, osm_cache, osm_downloader,
               road_matcher, track_cleaner, track_simplifier, utils)
from .config_loader import Config
from .coordinate_system import BBox, CoordinateSystem
from .database import Database
from .models import (CONF_NAO_IDENT, Alerta, FileResult, MatchedPoint,
                     SimplifyStats, Trecho)
from .road_matcher import GrafoVias

logger = utils.get_logger()


# ---------------------------------------------------------------------------
# Callbacks de progresso / cancelamento
# ---------------------------------------------------------------------------


@dataclass
class Reporter:
    """Canais opcionais de comunicacao com a interface."""
    log: Callable[[str], None] = lambda m: None
    etapa: Callable[[str], None] = lambda e: None
    prog_geral: Callable[[int, int], None] = lambda a, t: None
    prog_arquivo: Callable[[float], None] = lambda f: None
    cancelado: Callable[[], bool] = lambda: False
    arquivo_fim: Callable[[FileResult], None] = lambda fr: None

    def registrar(self, msg: str) -> None:
        logger.info(msg)
        try:
            self.log(msg)
        except Exception:  # noqa: BLE001 - callback da GUI nunca deve quebrar o nucleo
            pass


# ---------------------------------------------------------------------------
# Preparacao (sem rede)
# ---------------------------------------------------------------------------


@dataclass
class Preparo:
    fr: FileResult
    cs: CoordinateSystem
    tiles: list
    ok: bool = True


def _id_video(nome: str) -> str:
    m = re.match(r"(G[XHLP]\d{4,})", Path(nome).stem, re.IGNORECASE)
    return m.group(1) if m else Path(nome).stem


def hash_config(cfg: Config) -> str:
    relevante = {k: cfg.get(k) for k in (
        "buffer_ruas_m", "espacamento_pontos_m", "tolerancia_douglas_peucker_m",
        "angulo_preservacao_curva_graus", "raio_candidatos_m",
        "distancia_maxima_aceitavel_m", "permanencia_minima_trecho_m",
        "salto_velocidade_maxima_kmh", "tile_tamanho_km",
    )}
    relevante["matching"] = cfg.get("matching")
    blob = json.dumps(relevante, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def preparar_arquivo(caminho: str | Path, cfg: Config, rep: Reporter) -> Preparo:
    """Le, limpa e simplifica (sem acesso a rede). Nunca altera o GPX original."""
    caminho = Path(caminho)
    fr = FileResult(caminho=str(caminho), nome=caminho.name, id_video=_id_video(caminho.name))
    fr.data_processamento = _dt.datetime.now().replace(microsecond=0).isoformat()
    fr.config_usada = cfg.como_dict()

    try:
        fr.hash = utils.hash_arquivo(caminho)
    except OSError as e:
        fr.status = "erro"
        fr.adicionar_alerta("leitura", "ERRO", f"Nao foi possivel ler o arquivo: {e}",
                            "Verifique o caminho e as permissoes.")
        return Preparo(fr=fr, cs=None, tiles=[], ok=False)

    rep.etapa("Lendo GPX")
    _t = time.perf_counter()
    try:
        pontos, info = gpx_reader.ler_gpx(caminho)
    except Exception as e:  # noqa: BLE001 - arquivo corrompido/ilegivel: registra e segue
        fr.status = "erro"
        fr.adicionar_alerta("leitura", "ERRO", f"Falha ao interpretar GPX: {e}",
                            "Arquivo pode estar corrompido.")
        logger.exception("Falha lendo %s", caminho)
        return Preparo(fr=fr, cs=None, tiles=[], ok=False)

    fr.tempos_etapas["leitura_s"] = round(time.perf_counter() - _t, 3)
    fr.info = info
    fr.pontos_originais = pontos
    if not info.tem_tempo:
        fr.adicionar_alerta("leitura", "ALERTA",
                            "GPX sem horarios; horarios de entrada/saida ficarao vazios.",
                            "Verifique a gravacao da GoPro.")
    if not pontos:
        fr.status = "erro"
        fr.adicionar_alerta("leitura", "ERRO", "Nenhum ponto valido no GPX.",
                            "Arquivo vazio ou sem coordenadas validas.")
        return Preparo(fr=fr, cs=None, tiles=[], ok=False)

    rep.etapa("Limpando trajeto")
    _t = time.perf_counter()
    limpos, clean = track_cleaner.limpar(pontos, cfg)
    fr.tempos_etapas["limpeza_s"] = round(time.perf_counter() - _t, 3)
    fr.pontos_limpos = limpos
    fr.clean = clean
    if len(limpos) < 2:
        fr.status = "erro"
        fr.adicionar_alerta("limpeza", "ERRO",
                            "Menos de 2 pontos apos limpeza; impossivel formar trajeto.")
        return Preparo(fr=fr, cs=None, tiles=[], ok=False)

    rep.etapa("Simplificando trajeto")
    _t = time.perf_counter()
    cs = CoordinateSystem.para_pontos([p.as_lonlat() for p in limpos])
    simpl, ss = track_simplifier.simplificar(limpos, cfg, cs)
    fr.tempos_etapas["simplificacao_s"] = round(time.perf_counter() - _t, 3)
    ss.n_original = info.n_pontos
    fr.pontos_simplificados = simpl
    fr.simplify = ss

    tiles = osm_cache.tiles_para_pontos(
        [p.as_lonlat() for p in simpl],
        cfg.get("tile_tamanho_km"), cfg.get("buffer_ruas_m"))
    return Preparo(fr=fr, cs=cs, tiles=tiles, ok=True)


# ---------------------------------------------------------------------------
# Agrupamento em trechos (suavizacao de oscilacoes)
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    rid: Optional[str]
    idxs: list[int] = field(default_factory=list)


def _runs_de(rids: list[Optional[str]]) -> list[_Run]:
    runs: list[_Run] = []
    for i, r in enumerate(rids):
        if runs and runs[-1].rid == r:
            runs[-1].idxs.append(i)
        else:
            runs.append(_Run(r, [i]))
    return runs


def _dist_run(run: _Run, simpl) -> float:
    return sum(simpl[t].dist_prev_m for t in run.idxs)


def agrupar_trechos(matched: list[MatchedPoint], simpl, cfg: Config) -> list[Trecho]:
    """Agrupa pontos em trechos, removendo trocas falsas curtas (oscilacao).

    Regras de suavizacao (baseadas em distancia percorrida, nao em contagem):
      - Trecho curto (< permanencia_minima_trecho_m) entre duas ocorrencias da
        MESMA via (A-b-A) e absorvido -> vira A (oscilacao lateral do GPS).
      - Trecho curto entre vias DIFERENTES (A-b-C) e absorvido pelo vizinho de
        maior extensao (o "blip" e ruido).
      - Trechos >= limiar sao preservados, inclusive RETORNOS reais (A-B-A com
        B suficientemente longo permanece com as tres entradas).
    """
    min_perm = float(cfg.get("permanencia_minima_trecho_m", 20))
    rids: list[Optional[str]] = [
        (mp.escolhido.road_id if mp.escolhido else None) for mp in matched]

    # suavizacao iterativa
    for _ in range(100):
        runs = _runs_de(rids)
        alterou = False
        for i, run in enumerate(runs):
            if run.rid is None:
                continue
            if _dist_run(run, simpl) >= min_perm:
                continue
            prev_rid = runs[i - 1].rid if i > 0 else None
            next_rid = runs[i + 1].rid if i < len(runs) - 1 else None
            if prev_rid is not None and prev_rid == next_rid:
                repl = prev_rid
            elif prev_rid is not None and next_rid is not None:
                repl = prev_rid if _dist_run(runs[i - 1], simpl) >= _dist_run(runs[i + 1], simpl) \
                    else next_rid
            elif prev_rid is not None:
                repl = prev_rid
            elif next_rid is not None:
                repl = next_rid
            else:
                repl = run.rid  # cercado por nao-identificado: manter
            if repl != run.rid:
                for t in run.idxs:
                    rids[t] = repl
                alterou = True
                break
        if not alterou:
            break

    # construir trechos finais
    runs = _runs_de(rids)
    trechos: list[Trecho] = []
    ordem = 0
    for run in runs:
        ordem += 1
        tr = _montar_trecho(ordem, run, matched, simpl, cfg)
        trechos.append(tr)
    return trechos


def _montar_trecho(ordem: int, run: _Run, matched, simpl, cfg: Config) -> Trecho:
    idxs = run.idxs
    tr = Trecho(ordem=ordem, road_id=run.rid or "", indices=list(idxs))
    p_ini, p_fim = simpl[idxs[0]], simpl[idxs[-1]]
    tr.lat_ini, tr.lon_ini = p_ini.lat, p_ini.lon
    tr.lat_fim, tr.lon_fim = p_fim.lat, p_fim.lon
    tr.n_pontos = len(idxs)
    tr.hora_entrada = p_ini.tempo
    tr.hora_saida = p_fim.tempo
    if tr.hora_entrada and tr.hora_saida:
        tr.duracao_s = (tr.hora_saida - tr.hora_entrada).total_seconds()
    tr.distancia_m = _dist_run(run, simpl)

    if run.rid is None:
        tr.nome_via = "Nao identificado"
        tr.confianca = CONF_NAO_IDENT
        tr.observacoes = "Nenhuma via candidata dentro do raio configurado."
        tr.geometria_ajustada = [(simpl[t].lon, simpl[t].lat) for t in idxs]
        return tr

    # representante: candidato escolhido com este road_id e menor distancia
    reps = [matched[t].escolhido for t in idxs
            if matched[t].escolhido and matched[t].escolhido.road_id == run.rid]
    rep = min(reps, key=lambda c: c.dist_perp_m) if reps else \
        next((matched[t].escolhido for t in idxs if matched[t].escolhido), None)
    if rep is not None:
        tr.nome_via = rep.nome
        tr.ref = rep.ref
        tr.classe = rep.highway
        tr.osm_id = rep.osmid

    dists = [matched[t].dist_via_m for t in idxs if matched[t].dist_via_m is not None]
    tr.dist_media_m = sum(dists) / len(dists) if dists else 0.0
    tr.dist_max_m = max(dists) if dists else 0.0
    tr.geometria_ajustada = [
        (matched[t].escolhido.proj_lon, matched[t].escolhido.proj_lat)
        if matched[t].escolhido else (simpl[t].lon, simpl[t].lat)
        for t in idxs
    ]

    # metricas para confianca
    azdiffs = []
    margens = []
    for t in idxs:
        mp = matched[t]
        if mp.escolhido and mp.escolhido.azimute_via is not None and mp.tp.azimute is not None:
            azdiffs.append(utils.diff_angular(mp.tp.azimute, mp.escolhido.azimute_via))
        if mp.margem_score is not None:
            margens.append(mp.margem_score)
    azdiff_medio = sum(azdiffs) / len(azdiffs) if azdiffs else None
    margem_media = sum(margens) / len(margens) if margens else None
    if not p_ini.tempo:
        tr.observacoes = (tr.observacoes + " | sem horario no GPX").strip(" |")
    tr.confianca = confidence.classificar(tr, azdiff_medio, margem_media, cfg)
    return tr


# ---------------------------------------------------------------------------
# Gerenciador de grafos (cache por conjunto de tiles)
# ---------------------------------------------------------------------------


class GerenciadorGrafos:
    """Carrega e mantem em memoria poucos grafos (LRU) por conjunto de tiles."""

    def __init__(self, db: Database, max_em_memoria: int = 2):
        self.db = db
        self.max = max_em_memoria
        self._cache: "OrderedDict[frozenset, GrafoVias]" = OrderedDict()

    def obter(self, tiles: list) -> Optional[GrafoVias]:
        usaveis = [t for t in tiles
                   if (r := self.db.obter_tile(t.id)) and r["situacao"] == "ok"]
        if not usaveis:
            return None
        chave = frozenset(t.id for t in usaveis)
        if chave in self._cache:
            self._cache.move_to_end(chave)
            return self._cache[chave]
        G = osm_downloader.carregar_grafo(usaveis, self.db)
        if G is None:
            return None
        # projecao deterministica pelo bbox dos tiles (reuso entre arquivos)
        lo = min(t.bounds[0] for t in usaveis)
        la = min(t.bounds[1] for t in usaveis)
        lo2 = max(t.bounds[2] for t in usaveis)
        la2 = max(t.bounds[3] for t in usaveis)
        cs = CoordinateSystem.para_bbox(BBox(lo, la, lo2, la2))
        gv = GrafoVias(G, cs)
        self._cache[chave] = gv
        while len(self._cache) > self.max:
            self._cache.popitem(last=False)
        return gv


# ---------------------------------------------------------------------------
# Processamento em lote
# ---------------------------------------------------------------------------


class BatchProcessor:
    def __init__(self, cfg: Config, db: Database, rep: Reporter | None = None):
        self.cfg = cfg
        self.db = db
        self.rep = rep or Reporter()
        self.gestor = GerenciadorGrafos(db)

    def processar(self, arquivos: list[str | Path],
                  saida_dir: str | Path) -> list[FileResult]:
        rep = self.rep
        saida_dir = Path(saida_dir)
        saida_dir.mkdir(parents=True, exist_ok=True)
        resultados: list[FileResult] = []
        total = len(arquivos)

        # ---- Fase 1: preparar todos (sem rede) e acumular tiles ----
        rep.registrar(f"Preparando {total} arquivo(s)...")
        preparos: list[Preparo] = []
        tiles_todos = []
        for i, arq in enumerate(arquivos):
            if rep.cancelado():
                rep.registrar("Cancelado durante a preparacao.")
                return resultados
            rep.prog_geral(i, total)
            rep.registrar(f"[{i+1}/{total}] Preparando {Path(arq).name}")
            prep = preparar_arquivo(arq, self.cfg, rep)
            preparos.append(prep)
            if prep.ok:
                tiles_todos.append(prep.tiles)

        tiles_uniao = osm_cache.unir_tiles(*tiles_todos) if tiles_todos else []
        rep.registrar(f"Areas necessarias: {len(tiles_uniao)} tile(s).")

        # ---- Fase 2: garantir tiles (baixar somente ausentes) ----
        if tiles_uniao:
            rep.etapa("Obtendo malha viaria (OSM)")
            res_tiles = osm_downloader.garantir_tiles(
                self.db, tiles_uniao, self.cfg,
                cancelado=rep.cancelado,
                progresso=lambda m, a, t: (rep.registrar(m), rep.prog_arquivo(a / max(1, t))),
            )
            if res_tiles.faltando:
                rep.registrar(f"ATENCAO: {len(res_tiles.faltando)} tile(s) ausentes "
                              f"(offline/sem rede). Resultados podem ser parciais.")

        # ---- Fase 3: processar cada arquivo ----
        for i, (arq, prep) in enumerate(zip(arquivos, preparos)):
            if rep.cancelado():
                rep.registrar("Cancelado durante o processamento.")
                break
            rep.prog_geral(i, total)
            fr = prep.fr
            rep.registrar(f"[{i+1}/{total}] Processando {fr.nome}")
            if not prep.ok:
                resultados.append(fr)
                rep.arquivo_fim(fr)
                continue
            try:
                self._processar_um(prep, saida_dir, rep)
            except Exception as e:  # noqa: BLE001 - nunca derrubar o lote
                fr.status = "erro"
                fr.adicionar_alerta("processamento", "ERRO", str(e),
                                    "Ver log para detalhes tecnicos.")
                logger.exception("Erro processando %s", fr.nome)
            resultados.append(fr)
            rep.arquivo_fim(fr)

        rep.prog_geral(total, total)

        # ---- Excel consolidado ----
        if bool(self.cfg.get("saidas.gerar_excel", True)) and resultados:
            try:
                from . import excel_exporter
                caminho_xlsx = excel_exporter.exportar(resultados, self.cfg, saida_dir)
                rep.registrar(f"Excel consolidado: {caminho_xlsx}")
            except Exception as e:  # noqa: BLE001
                rep.registrar(f"Falha ao gerar Excel: {e}")
                logger.exception("Falha no Excel consolidado")

        return resultados

    def _processar_um(self, prep: Preparo, saida_dir: Path, rep: Reporter) -> None:
        fr = prep.fr
        cfg = self.cfg

        # duplicidade (mesmo conteudo + mesma config)
        chash = hash_config(cfg)
        ja = self.db.buscar_resultado(fr.hash, chash)
        if ja is not None:
            fr.adicionar_alerta("banco", "INFO",
                                f"Arquivo ja processado antes (id {ja['id']}, "
                                f"{ja['data_processamento']}).",
                                "Reprocessando conforme configuracao atual.")

        rep.etapa("Carregando malha viaria")
        gv = self.gestor.obter(prep.tiles)
        if gv is None:
            fr.status = "parcial"
            fr.adicionar_alerta("osm", "ALERTA",
                                "Sem malha viaria disponivel para a area (cache incompleto).",
                                "Rode online ou atualize o cache para completar.")
            # ainda assim registra o preparo
            self._registrar_db(fr, chash)
            return
        fr.versao_osm = self._versao_osm(prep.tiles)

        rep.etapa("Map matching (HMM/Viterbi)")
        _t = time.perf_counter()
        fr.matched = road_matcher.map_match(fr.pontos_simplificados, gv, cfg)
        fr.tempos_etapas["matching_s"] = round(time.perf_counter() - _t, 3)

        rep.etapa("Agrupando vias")
        _t = time.perf_counter()
        fr.trechos = agrupar_trechos(fr.matched, fr.pontos_simplificados, cfg)
        fr.tempos_etapas["agrupamento_s"] = round(time.perf_counter() - _t, 3)

        # status
        n_ident = sum(1 for t in fr.trechos if t.road_id)
        if n_ident == 0:
            fr.status = "parcial"
        else:
            fr.status = "ok"

        rep.etapa("Gerando saidas")
        self._gerar_saidas(fr, cfg, saida_dir)
        self._registrar_db(fr, chash)
        rep.registrar(f"  -> {len(fr.trechos)} trecho(s), status {fr.status}")

    def _gerar_saidas(self, fr: FileResult, cfg: Config, saida_dir: Path) -> None:
        if bool(cfg.get("saidas.gerar_mapa_html", True)):
            try:
                from . import map_exporter
                fr.caminho_mapa = str(map_exporter.exportar(fr, cfg, saida_dir))
            except Exception as e:  # noqa: BLE001
                fr.adicionar_alerta("saida", "ALERTA", f"Falha ao gerar mapa: {e}")
                logger.exception("Falha no mapa de %s", fr.nome)
        if bool(cfg.get("saidas.gerar_geopackage", False)):
            try:
                from . import map_exporter
                fr.caminho_geopackage = str(map_exporter.exportar_geopackage(fr, saida_dir))
            except Exception as e:  # noqa: BLE001
                fr.adicionar_alerta("saida", "ALERTA", f"Falha ao gerar GeoPackage: {e}")
                logger.exception("Falha no GeoPackage de %s", fr.nome)
        if bool(cfg.get("saidas.gerar_json", True)):
            try:
                fr.caminho_json = str(_exportar_json(fr, saida_dir))
            except Exception as e:  # noqa: BLE001
                fr.adicionar_alerta("saida", "ALERTA", f"Falha ao gerar JSON: {e}")
                logger.exception("Falha no JSON de %s", fr.nome)

    def _registrar_db(self, fr: FileResult, chash: str) -> None:
        try:
            self.db.registrar_arquivo(
                caminho=fr.caminho, nome=fr.nome, hash_arquivo=fr.hash,
                config_hash=chash, config=fr.config_usada, status=fr.status,
                n_pontos=(fr.info.n_pontos if fr.info else 0),
                n_trechos=len(fr.trechos),
                distancia_m=(fr.info.distancia_total_m if fr.info else 0.0),
                resumo=fr.resumo_confianca(),
                versao_programa=cfg_versao(self.cfg),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Falha ao registrar no banco: %s", e)

    def _versao_osm(self, tiles) -> str:
        datas = []
        for t in tiles:
            row = self.db.obter_tile(t.id)
            if row and row["versao_base"]:
                datas.append(row["versao_base"])
        return max(datas) if datas else ""


def cfg_versao(cfg: Config) -> str:
    return str(cfg.get("versao_programa", "1.0.0"))


# ---------------------------------------------------------------------------
# JSON tecnico por arquivo
# ---------------------------------------------------------------------------


def _exportar_json(fr: FileResult, saida_dir: Path) -> Path:
    def dt(x):
        return x.isoformat() if x else None

    dados = {
        "programa": {"nome": "GPX Analisador de Rotas",
                     "versao": fr.config_usada.get("versao_programa", "1.0.0")},
        "data_processamento": fr.data_processamento,
        "versao_osm": fr.versao_osm,
        "arquivo": {"caminho": fr.caminho, "nome": fr.nome, "id_video": fr.id_video,
                    "hash": fr.hash, "status": fr.status},
        "metadados_gpx": _info_dict(fr),
        "configuracoes": fr.config_usada,
        "estatisticas": {
            "pontos_originais": fr.simplify.n_original if fr.simplify else 0,
            "pontos_apos_limpeza": fr.simplify.n_apos_limpeza if fr.simplify else 0,
            "pontos_apos_simplificacao": fr.simplify.n_apos_simplificacao if fr.simplify else 0,
            "reducao_pct": round(fr.simplify.reducao_pct, 2) if fr.simplify else 0,
            "confianca": fr.resumo_confianca(),
            "tempos_etapas_s": fr.tempos_etapas,
        },
        "vias_percorridas": [
            {
                "ordem": t.ordem, "nome": t.nome_via, "ref": t.ref, "classe": t.classe,
                "osm_id": t.osm_id, "hora_entrada": dt(t.hora_entrada),
                "hora_saida": dt(t.hora_saida), "duracao_s": t.duracao_s,
                "distancia_m": round(t.distancia_m, 1), "n_pontos": t.n_pontos,
                "dist_media_m": round(t.dist_media_m, 2), "dist_max_m": round(t.dist_max_m, 2),
                "confianca": t.confianca, "observacoes": t.observacoes,
                "lat_ini": t.lat_ini, "lon_ini": t.lon_ini,
                "lat_fim": t.lat_fim, "lon_fim": t.lon_fim,
            } for t in fr.trechos
        ],
        "alertas": [
            {"etapa": a.etapa, "tipo": a.tipo, "mensagem": a.mensagem, "acao": a.acao}
            for a in fr.alertas
        ],
    }
    destino = utils.caminho_unico(saida_dir / f"{utils.nome_seguro(Path(fr.nome).stem)}.json")
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    return destino


def _info_dict(fr: FileResult) -> dict:
    if not fr.info:
        return {}
    i = fr.info
    return {
        "n_trilhas": i.n_trilhas, "n_segmentos": i.n_segmentos, "n_pontos": i.n_pontos,
        "tempo_inicial": i.tempo_inicial.isoformat() if i.tempo_inicial else None,
        "tempo_final": i.tempo_final.isoformat() if i.tempo_final else None,
        "duracao_s": i.duracao_s, "distancia_total_m": round(i.distancia_total_m, 1),
        "bbox": [i.lon_min, i.lat_min, i.lon_max, i.lat_max],
        "campos": i.campos, "tem_tempo": i.tem_tempo, "anomalias": i.anomalias,
    }
