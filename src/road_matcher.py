"""Map matching sequencial (HMM/Viterbi) do trajeto sobre a malha viaria.

Este e o componente central. Em vez de associar cada ponto a via mais proxima
isoladamente, buscamos a SEQUENCIA de vias globalmente mais provavel, ao estilo
Newson & Krumm (2009):

  - Estados (por ponto): vias candidatas dentro de um raio configuravel.
  - Probabilidade de EMISSAO: quao bem o ponto "cai" sobre a via - combina a
    distancia perpendicular (ruido lateral do GPS) e a diferenca entre a direcao
    do veiculo e a direcao da via, mais um pequeno bonus pela classe da via.
  - Probabilidade de TRANSICAO: quao plausivel e ir de uma via candidata (ponto
    t) para outra (ponto t+1) - compara a distancia percorrida sobre a REDE
    (menor caminho no grafo, respeitando conectividade e sentido) com a
    distancia em linha reta entre os pontos. Transicoes impossiveis (sem caminho
    dentro de um corte) sao fortemente penalizadas.
  - Viterbi encontra a sequencia de maxima verossimilhanca.

Trabalha em coordenadas metricas (UTM). Arquitetura isolada: para trocar por
outro algoritmo, basta reimplementar `map_match` mantendo a assinatura.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import networkx as nx
import shapely
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from . import utils
from .config_loader import Config
from .coordinate_system import CoordinateSystem
from .models import Candidato, MatchedPoint, TrackPoint

logger = utils.get_logger()

NEG_INF = -1e9


# ---------------------------------------------------------------------------
# Identidade da via (nome de exibicao + chave agrupavel)
# ---------------------------------------------------------------------------

def _primeiro(v):
    """OSMnx pode devolver listas (arestas mescladas); pega o 1o valor util."""
    if isinstance(v, (list, tuple)):
        for x in v:
            if x:
                return x
        return None
    return v


def identidade_via(dados: dict) -> tuple[str, str, str, str]:
    """Resolve (road_id, nome_exibicao, ref, highway) a partir dos atributos.

    Prioridade de nome: name > official_name > ref > alt_name >
    classe+OSM ID > 'Via sem nome'. O road_id (normalizado) e usado apenas para
    agrupar; o nome de exibicao preserva o original.
    """
    name = _primeiro(dados.get("name"))
    official = _primeiro(dados.get("official_name"))
    ref = _primeiro(dados.get("ref")) or ""
    alt = _primeiro(dados.get("alt_name"))
    highway = _primeiro(dados.get("highway")) or ""
    osmid = _primeiro(dados.get("osmid"))

    if name:
        return (f"name:{utils.normalizar_nome_via(name)}", str(name), str(ref), str(highway))
    if official:
        return (f"name:{utils.normalizar_nome_via(official)}", str(official), str(ref), str(highway))
    if ref:
        return (f"ref:{utils.normalizar_nome_via(ref)}", str(ref), str(ref), str(highway))
    if alt:
        return (f"name:{utils.normalizar_nome_via(alt)}", str(alt), str(ref), str(highway))
    # via sem nome
    classe = highway or "via"
    if osmid is not None:
        return (f"osm:{osmid}", f"Via sem nome — OSM {osmid}", str(ref), str(highway))
    return (f"classe:{classe}", f"Via sem nome — {classe}", str(ref), str(highway))


# ---------------------------------------------------------------------------
# Representacao da malha viaria para matching
# ---------------------------------------------------------------------------


@dataclass
class ArestaInfo:
    idx: int
    u: int
    v: int
    k: int
    geom_m: LineString
    length_m: float
    road_id: str
    nome: str
    ref: str
    highway: str
    osmid: object


class GrafoVias:
    """Malha viaria projetada em metros, com indice espacial e distancias de rede."""

    def __init__(self, G: nx.MultiDiGraph, cs: CoordinateSystem):
        self.G = G
        self.cs = cs
        self.arestas: list[ArestaInfo] = []
        self._node_xy: dict[int, tuple[float, float]] = {}
        self._dijkstra_cache: dict[tuple[int, float], dict[int, float]] = {}
        self._construir()

    def _node_m(self, n) -> tuple[float, float]:
        if n not in self._node_xy:
            d = self.G.nodes[n]
            self._node_xy[n] = self.cs.to_m(float(d["x"]), float(d["y"]))
        return self._node_xy[n]

    def _construir(self) -> None:
        geoms = []
        for idx, (u, v, k, dados) in enumerate(self.G.edges(keys=True, data=True)):
            geom = dados.get("geometry")
            if geom is None:
                xu, yu = float(self.G.nodes[u]["x"]), float(self.G.nodes[u]["y"])
                xv, yv = float(self.G.nodes[v]["x"]), float(self.G.nodes[v]["y"])
                geom = LineString([(xu, yu), (xv, yv)])
            geom_m = shapely.ops.transform(self.cs.fn_to_m, geom)
            length_m = dados.get("length")
            try:
                length_m = float(length_m)
            except (TypeError, ValueError):
                length_m = geom_m.length
            road_id, nome, ref, highway = identidade_via(dados)
            self.arestas.append(ArestaInfo(
                idx=idx, u=u, v=v, k=k, geom_m=geom_m, length_m=length_m,
                road_id=road_id, nome=nome, ref=ref, highway=highway,
                osmid=_primeiro(dados.get("osmid")),
            ))
            geoms.append(geom_m)
        self.strtree = STRtree(geoms) if geoms else None
        logger.info("GrafoVias: %d arestas indexadas", len(self.arestas))

    # ------- candidatos -------
    def candidatos(self, x: float, y: float, azimute: Optional[float],
                   cfg: Config) -> list[Candidato]:
        if self.strtree is None:
            return []
        raio = float(cfg.get("raio_candidatos_m", 30))
        raio_max = float(cfg.get("distancia_maxima_aceitavel_m", 50))
        max_cand = int(cfg.get("matching.max_candidatos_por_ponto", 8))
        sigma = float(cfg.get("matching.sigma_gps_m", 12))
        peso_az = float(cfg.get("matching.peso_azimute", 1.0))
        sigma_az = float(cfg.get("matching.sigma_azimute_graus", 45))
        bonus = cfg.get("matching.bonus_classe_via", {}) or {}

        p = Point(x, y)
        idxs = self.strtree.query(p.buffer(raio_max))
        brutos = []
        for i in idxs:
            ar = self.arestas[int(i)]
            d = ar.geom_m.distance(p)
            if d <= raio_max:
                brutos.append((d, ar))
        # preferir os que estao dentro do raio "bom"; se nenhum, usar ate o max
        dentro = [b for b in brutos if b[0] <= raio]
        usar = dentro if dentro else brutos
        usar.sort(key=lambda b: b[0])
        usar = usar[:max_cand]

        cands: list[Candidato] = []
        for d, ar in usar:
            pos = ar.geom_m.project(p)
            frac = pos / ar.length_m if ar.length_m > 0 else 0.0
            proj = ar.geom_m.interpolate(pos)
            azv = self._bearing_em(ar.geom_m, pos)
            lonlat = self.cs.to_wgs(proj.x, proj.y)
            # emissao (log-prob): ruido lateral (distancia perpendicular)
            emiss = -0.5 * (d / max(1e-3, sigma)) ** 2
            if azimute is not None and azv is not None and peso_az > 0:
                dtheta = utils.diff_angular(azimute, azv)
                emiss += peso_az * (-0.5 * (dtheta / max(1e-3, sigma_az)) ** 2)
            emiss += float(bonus.get(ar.highway, 0.0))
            cands.append(Candidato(
                edge_key=(ar.u, ar.v, ar.k), road_id=ar.road_id, nome=ar.nome,
                ref=ar.ref, highway=ar.highway, osmid=ar.osmid,
                dist_perp_m=d, azimute_via=azv, fracao=frac,
                proj_lon=lonlat[0], proj_lat=lonlat[1], score_emissao=emiss,
            ))
        return cands

    @staticmethod
    def _bearing_em(geom_m: LineString, pos: float) -> Optional[float]:
        """Azimute (0=N, UTM) do segmento que contem a posicao 'pos' ao longo da via."""
        coords = list(geom_m.coords)
        if len(coords) < 2:
            return None
        acum = 0.0
        for a, b in zip(coords[:-1], coords[1:]):
            seg = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
            if acum + seg >= pos or (a, b) == (coords[-2], coords[-1]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
            acum += seg
        return None

    # ------- distancias de rede (dijkstra com corte, cacheado) -------
    def dist_desde(self, node: int, cutoff: float) -> dict[int, float]:
        chave = (node, round(cutoff, 1))
        if chave in self._dijkstra_cache:
            return self._dijkstra_cache[chave]
        try:
            dists = nx.single_source_dijkstra_path_length(
                self.G, node, cutoff=cutoff, weight="length")
        except (nx.NodeNotFound, nx.NetworkXError):
            dists = {node: 0.0}
        # limitar tamanho do cache
        if len(self._dijkstra_cache) < 5000:
            self._dijkstra_cache[chave] = dists
        return dists


# ---------------------------------------------------------------------------
# Viterbi
# ---------------------------------------------------------------------------


def map_match(simpl: list[TrackPoint], gv: GrafoVias, cfg: Config) -> list[MatchedPoint]:
    """Executa o map matching e retorna um MatchedPoint por ponto simplificado."""
    n = len(simpl)
    resultado: list[MatchedPoint] = [MatchedPoint(indice=i, tp=simpl[i]) for i in range(n)]
    if n == 0 or gv.strtree is None:
        for mp in resultado:
            mp.motivo_descarte = "sem malha viaria disponivel"
        return resultado

    beta = float(cfg.get("matching.beta_transicao_m", 12))
    fator_corte = float(cfg.get("matching.fator_corte_rota", 3.0))
    corte_min = float(cfg.get("matching.corte_rota_minimo_m", 60.0))

    # coordenadas metricas dos pontos
    xy = gv.cs.pontos_to_m([p.as_lonlat() for p in simpl])

    # candidatos por ponto
    cands: list[list[Candidato]] = []
    for i, p in enumerate(simpl):
        c = gv.candidatos(xy[i][0], xy[i][1], p.azimute, cfg)
        cands.append(c)
        # registrar melhor/segundo local para diagnostico e confianca
        if c:
            ordenados = sorted(c, key=lambda z: z.score_emissao, reverse=True)
            resultado[i].dist_via_m = min(z.dist_perp_m for z in c)
            if len(ordenados) >= 2:
                resultado[i].margem_score = ordenados[0].score_emissao - ordenados[1].score_emissao
        else:
            resultado[i].motivo_descarte = "sem via candidata no raio"

    # rodar Viterbi por trechos contiguos que possuem candidatos
    i = 0
    while i < n:
        if not cands[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and cands[j + 1]:
            j += 1
        _viterbi_run(simpl, xy, cands, resultado, i, j, gv, beta, fator_corte, corte_min)
        i = j + 1

    return resultado


def _viterbi_run(simpl, xy, cands, resultado, ini, fim, gv, beta, fator_corte, corte_min):
    """Viterbi para o trecho [ini..fim] (inclusive) de pontos com candidatos."""
    # V[t] = lista de scores; BP[t] = lista de indices do estado anterior
    prev_scores = [c.score_emissao for c in cands[ini]]
    prev_states = cands[ini]
    backptr: list[list[int]] = [[-1] * len(prev_states)]
    todos_estados = [prev_states]

    for t in range(ini + 1, fim + 1):
        cur = cands[t]
        d_gps = math.dist(xy[t - 1], xy[t])
        cutoff = max(corte_min, fator_corte * d_gps + corte_min)
        # pre-calcular dijkstra a partir do 'v' de cada estado anterior
        dist_maps = [gv.dist_desde(s.edge_key[1], cutoff) for s in prev_states]
        len_prev = [_len_edge(gv, s) for s in prev_states]
        len_cur = [_len_edge(gv, s) for s in cur]

        cur_scores = [NEG_INF] * len(cur)
        cur_bp = [-1] * len(cur)
        for b_i, b in enumerate(cur):
            u_b = b.edge_key[0]
            melhor = NEG_INF
            melhor_a = -1
            for a_i, a in enumerate(prev_states):
                if prev_scores[a_i] <= NEG_INF / 2:
                    continue
                d_route = _dist_rota(a, b, len_prev[a_i], len_cur[b_i], dist_maps[a_i])
                if d_route is None:
                    trans = NEG_INF
                else:
                    trans = -abs(d_route - d_gps) / max(1e-3, beta)
                sc = prev_scores[a_i] + trans
                if sc > melhor:
                    melhor = sc
                    melhor_a = a_i
            cur_scores[b_i] = melhor + b.score_emissao
            cur_bp[b_i] = melhor_a
        backptr.append(cur_bp)
        todos_estados.append(cur)
        prev_scores = cur_scores
        prev_states = cur
        # se todos NEG_INF (trecho impossivel), reinicia com emissao pura
        if all(s <= NEG_INF / 2 for s in prev_scores):
            prev_scores = [c.score_emissao for c in cur]
            backptr[-1] = [-1] * len(cur)

    # backtrack
    ultimo = max(range(len(prev_scores)), key=lambda k: prev_scores[k])
    caminho = [ultimo]
    for t in range(len(todos_estados) - 1, 0, -1):
        ultimo = backptr[t][ultimo]
        if ultimo < 0:
            ultimo = 0
        caminho.append(ultimo)
    caminho.reverse()

    for offset, estado_idx in enumerate(caminho):
        t = ini + offset
        estados = todos_estados[offset]
        escolhido = estados[estado_idx]
        resultado[t].escolhido = escolhido
        resultado[t].dist_via_m = escolhido.dist_perp_m
        # segundo melhor local (por emissao) para diagnostico/confianca
        outros = sorted([c for c in cands[t] if c is not escolhido],
                        key=lambda z: z.score_emissao, reverse=True)
        if outros:
            resultado[t].segundo = outros[0]
            resultado[t].margem_score = escolhido.score_emissao - outros[0].score_emissao


def _len_edge(gv: GrafoVias, cand: Candidato) -> float:
    u, v, k = cand.edge_key
    dados = gv.G.get_edge_data(u, v, k) or {}
    try:
        return float(dados.get("length"))
    except (TypeError, ValueError):
        return 1.0


def _dist_rota(a: Candidato, b: Candidato, len_a: float, len_b: float,
               dist_map: dict[int, float]) -> Optional[float]:
    """Distancia sobre a rede entre a projecao de 'a' e a de 'b'."""
    if a.edge_key == b.edge_key:
        # mesma aresta: progresso ao longo dela
        return abs(b.fracao - a.fracao) * len_a
    u_b = b.edge_key[0]
    v_a = a.edge_key[1]
    if v_a not in dist_map and u_b not in dist_map:
        return None
    d_no = dist_map.get(u_b)
    if d_no is None:
        return None
    resto_a = len_a * (1.0 - a.fracao)
    inicio_b = len_b * b.fracao
    return resto_a + d_no + inicio_b
