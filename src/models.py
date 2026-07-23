"""Modelo de dados (dataclasses) compartilhado por todo o pipeline.

Estas estruturas trafegam entre os modulos: leitura -> limpeza -> simplificacao
-> matching -> agrupamento -> relatorios. Nenhuma delas depende de bibliotecas
pesadas, para serem faceis de testar e serializar.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Nivel de confianca
# ---------------------------------------------------------------------------

CONF_ALTA = "Alta"
CONF_MEDIA = "Media"
CONF_BAIXA = "Baixa"
CONF_NAO_IDENT = "Nao identificado"

ORDEM_CONFIANCA = {CONF_ALTA: 3, CONF_MEDIA: 2, CONF_BAIXA: 1, CONF_NAO_IDENT: 0}


# ---------------------------------------------------------------------------
# Pontos do trajeto
# ---------------------------------------------------------------------------


@dataclass
class TrackPoint:
    """Um ponto de GPS lido do GPX, com metricas derivadas."""
    indice: int                      # ordem original (0-based) no arquivo
    lat: float
    lon: float
    ele: Optional[float] = None
    tempo: Optional[_dt.datetime] = None   # UTC (timezone-aware) ou None
    fix: Optional[str] = None
    hdop: Optional[float] = None
    trilha: int = 0
    segmento: int = 0

    # metricas derivadas (preenchidas pelo gpx_reader)
    dist_prev_m: float = 0.0         # distancia ate o ponto anterior
    dist_acum_m: float = 0.0         # distancia acumulada desde o inicio
    vel_kmh: Optional[float] = None
    azimute: Optional[float] = None  # direcao do deslocamento (graus, 0=N)

    def as_lonlat(self) -> tuple[float, float]:
        return (self.lon, self.lat)


@dataclass
class GpxInfo:
    """Metadados de inspecao de um arquivo GPX."""
    caminho: str
    nome: str
    n_trilhas: int = 0
    n_segmentos: int = 0
    n_pontos: int = 0
    tempo_inicial: Optional[_dt.datetime] = None
    tempo_final: Optional[_dt.datetime] = None
    duracao_s: Optional[float] = None
    distancia_total_m: float = 0.0
    lat_min: Optional[float] = None
    lat_max: Optional[float] = None
    lon_min: Optional[float] = None
    lon_max: Optional[float] = None
    campos: list[str] = field(default_factory=list)
    tem_tempo: bool = True
    anomalias: list[str] = field(default_factory=list)


@dataclass
class CleanStats:
    """Estatisticas da etapa de limpeza."""
    n_entrada: int = 0
    n_saida: int = 0
    removidos_duplicados: int = 0
    removidos_invalidos: int = 0
    removidos_salto: int = 0
    removidos_parado: int = 0
    detalhes: list[str] = field(default_factory=list)

    @property
    def total_removidos(self) -> int:
        return self.n_entrada - self.n_saida


@dataclass
class SimplifyStats:
    """Estatisticas da etapa de simplificacao."""
    n_original: int = 0
    n_apos_limpeza: int = 0
    n_apos_simplificacao: int = 0

    @property
    def reducao_pct(self) -> float:
        if self.n_original <= 0:
            return 0.0
        return 100.0 * (1.0 - self.n_apos_simplificacao / self.n_original)


# ---------------------------------------------------------------------------
# Vias e matching
# ---------------------------------------------------------------------------


@dataclass
class Candidato:
    """Uma via candidata para um ponto do trajeto (aresta do grafo OSM)."""
    edge_key: tuple                  # (u, v, k) identifica a aresta no grafo
    road_id: str                     # identidade agrupavel da via (nome/ref/osm)
    nome: str                        # nome de exibicao (original)
    ref: str = ""
    highway: str = ""
    osmid: Any = None
    dist_perp_m: float = 0.0         # distancia perpendicular ponto->via
    azimute_via: Optional[float] = None
    fracao: float = 0.0              # posicao ao longo da aresta (0..1)
    proj_lon: float = 0.0            # ponto projetado na via (para o mapa)
    proj_lat: float = 0.0
    score_emissao: float = 0.0       # log-prob de emissao (preenchido no matcher)


@dataclass
class MatchedPoint:
    """Resultado do map matching para um ponto simplificado."""
    indice: int                      # indice no trajeto simplificado
    tp: TrackPoint
    escolhido: Optional[Candidato] = None
    segundo: Optional[Candidato] = None
    margem_score: Optional[float] = None   # diferenca de score p/ o 2o melhor
    dist_via_m: Optional[float] = None
    motivo_descarte: str = ""


@dataclass
class Trecho:
    """Um trecho consolidado: permanencia continua em uma mesma via."""
    ordem: int = 0
    road_id: str = ""
    nome_via: str = "Via sem nome"
    ref: str = ""
    classe: str = ""
    osm_id: Any = None
    hora_entrada: Optional[_dt.datetime] = None
    hora_saida: Optional[_dt.datetime] = None
    duracao_s: Optional[float] = None
    distancia_m: float = 0.0
    lat_ini: Optional[float] = None
    lon_ini: Optional[float] = None
    lat_fim: Optional[float] = None
    lon_fim: Optional[float] = None
    n_pontos: int = 0
    dist_media_m: float = 0.0
    dist_max_m: float = 0.0
    confianca: str = CONF_MEDIA
    observacoes: str = ""
    # indices (no trajeto simplificado) que compoem este trecho
    indices: list[int] = field(default_factory=list)
    # geometria ajustada (lista de (lon,lat)) para o mapa
    geometria_ajustada: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Alerta:
    """Erro ou alerta associado a uma etapa/arquivo."""
    arquivo: str
    etapa: str
    tipo: str           # "ERRO", "ALERTA", "INFO"
    mensagem: str
    data_hora: str = ""
    acao: str = ""


# ---------------------------------------------------------------------------
# Resultado por arquivo
# ---------------------------------------------------------------------------


@dataclass
class FileResult:
    """Resultado completo do processamento de um arquivo GPX."""
    caminho: str
    nome: str
    id_video: str = ""
    hash: str = ""
    status: str = "pendente"     # "ok", "parcial", "erro", "cancelado"
    info: Optional[GpxInfo] = None
    clean: Optional[CleanStats] = None
    simplify: Optional[SimplifyStats] = None

    pontos_originais: list[TrackPoint] = field(default_factory=list)
    pontos_limpos: list[TrackPoint] = field(default_factory=list)
    pontos_simplificados: list[TrackPoint] = field(default_factory=list)
    matched: list[MatchedPoint] = field(default_factory=list)
    trechos: list[Trecho] = field(default_factory=list)
    alertas: list[Alerta] = field(default_factory=list)

    tempos_etapas: dict[str, float] = field(default_factory=dict)
    config_usada: dict[str, Any] = field(default_factory=dict)
    versao_osm: str = ""
    data_processamento: str = ""

    # caminhos das saidas geradas
    caminho_mapa: str = ""
    caminho_geopackage: str = ""
    caminho_json: str = ""

    # ------- contagens de confianca -------
    def contar_confianca(self, nivel: str) -> int:
        return sum(1 for t in self.trechos if t.confianca == nivel)

    def resumo_confianca(self) -> dict[str, int]:
        return {
            CONF_ALTA: self.contar_confianca(CONF_ALTA),
            CONF_MEDIA: self.contar_confianca(CONF_MEDIA),
            CONF_BAIXA: self.contar_confianca(CONF_BAIXA),
            CONF_NAO_IDENT: self.contar_confianca(CONF_NAO_IDENT),
        }

    def adicionar_alerta(self, etapa: str, tipo: str, mensagem: str, acao: str = "") -> None:
        self.alertas.append(Alerta(
            arquivo=self.nome, etapa=etapa, tipo=tipo,
            mensagem=mensagem, acao=acao,
        ))
