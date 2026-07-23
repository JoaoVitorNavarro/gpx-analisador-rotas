"""Carregamento, validacao e mesclagem das configuracoes (config/configuracoes.json).

As configuracoes do arquivo sao mescladas sobre um conjunto de padroes internos,
garantindo que chaves ausentes nunca quebrem o programa. A GUI pode sobrescrever
valores em tempo de execucao (ex.: modo_offline) sem alterar o arquivo.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from . import utils

logger = utils.get_logger()

PADROES: dict[str, Any] = {
    "buffer_ruas_m": 300,
    "espacamento_pontos_m": 10,
    "tolerancia_douglas_peucker_m": 3,
    "angulo_preservacao_curva_graus": 20,
    "raio_candidatos_m": 30,
    "distancia_maxima_aceitavel_m": 50,
    "tile_tamanho_km": 5,
    "cache_validade_dias": 90,
    "permanencia_minima_trecho_m": 20,
    "salto_velocidade_maxima_kmh": 200,
    "gerar_diagnostico_pontos": False,
    "modo_offline": False,
    "atualizar_cache": False,
    "limpeza": {
        "distancia_duplicado_m": 0.5,
        "parado_velocidade_kmh": 1.5,
        "parado_janela_min_pontos": 5,
        "hdop_maximo": 30.0,
    },
    "matching": {
        "sigma_gps_m": 12.0,
        "beta_transicao_m": 12.0,
        "peso_azimute": 1.0,
        "sigma_azimute_graus": 45.0,
        "max_candidatos_por_ponto": 8,
        "fator_corte_rota": 3.0,
        "corte_rota_minimo_m": 60.0,
        "bonus_classe_via": {
            "motorway": 0.6, "trunk": 0.5, "primary": 0.4, "secondary": 0.3,
            "tertiary": 0.2, "residential": 0.1, "unclassified": 0.0,
            "service": -0.2, "living_street": 0.0,
        },
    },
    "confianca": {
        "dist_media_alta_m": 8.0,
        "dist_media_media_m": 18.0,
        "dist_max_alta_m": 20.0,
        "dif_azimute_alta_graus": 25.0,
        "dif_azimute_media_graus": 55.0,
        "pontos_minimos_confiavel": 3,
        "margem_candidato_alta": 1.5,
    },
    "saidas": {
        "gerar_excel": True,
        "gerar_mapa_html": True,
        "gerar_geopackage": False,
        "gerar_json": True,
        "abrir_pasta_ao_terminar": False,
    },
    "osm": {
        "network_type": "drive",
        "overpass_timeout_s": 180,
        "pausa_entre_downloads_s": 1.0,
        "max_downloads_simultaneos": 1,
    },
    "versao_programa": "1.0.0",
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Acesso as configuracoes com validacao e caminhos por ponto (dot-path)."""

    def __init__(self, dados: dict[str, Any], origem: Path | None = None):
        self._dados = dados
        self.origem = origem
        self.validar()

    # ------- acesso -------
    def get(self, caminho: str, padrao: Any = None) -> Any:
        no: Any = self._dados
        for parte in caminho.split("."):
            if isinstance(no, dict) and parte in no:
                no = no[parte]
            else:
                return padrao
        return no

    def set(self, caminho: str, valor: Any) -> None:
        partes = caminho.split(".")
        no = self._dados
        for p in partes[:-1]:
            no = no.setdefault(p, {})
        no[partes[-1]] = valor

    def como_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._dados)

    # ------- validacao -------
    def validar(self) -> None:
        """Valida limites; corrige valores absurdos e registra avisos."""
        positivos = [
            "buffer_ruas_m", "espacamento_pontos_m", "tolerancia_douglas_peucker_m",
            "raio_candidatos_m", "distancia_maxima_aceitavel_m", "tile_tamanho_km",
            "cache_validade_dias", "permanencia_minima_trecho_m",
            "salto_velocidade_maxima_kmh",
        ]
        for chave in positivos:
            v = self.get(chave)
            if v is None or (isinstance(v, (int, float)) and v <= 0):
                logger.warning("Config '%s' invalida (%r); usando padrao %r",
                               chave, v, PADROES.get(chave))
                self.set(chave, PADROES.get(chave))
        ang = self.get("angulo_preservacao_curva_graus")
        if not (0 < float(ang) < 180):
            self.set("angulo_preservacao_curva_graus", PADROES["angulo_preservacao_curva_graus"])
        if self.get("raio_candidatos_m") > self.get("distancia_maxima_aceitavel_m"):
            logger.warning("raio_candidatos_m > distancia_maxima_aceitavel_m; "
                           "ampliando distancia maxima.")
            self.set("distancia_maxima_aceitavel_m", self.get("raio_candidatos_m"))

    # ------- persistencia -------
    def salvar(self, destino: Path | None = None) -> Path:
        destino = Path(destino or self.origem or utils.default_config_path())
        destino.parent.mkdir(parents=True, exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(self._dados, f, ensure_ascii=False, indent=2)
        return destino


def carregar_config(caminho: str | Path | None = None) -> Config:
    """Carrega configuracoes de um arquivo (ou padroes se ausente/invalido)."""
    origem = Path(caminho) if caminho else utils.default_config_path()
    dados = copy.deepcopy(PADROES)
    if origem.exists():
        try:
            with open(origem, "r", encoding="utf-8") as f:
                arquivo = json.load(f)
            dados = _merge(dados, arquivo)
            logger.info("Configuracoes carregadas de %s", origem)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Falha ao ler config %s (%s); usando padroes.", origem, e)
    else:
        logger.warning("Config %s inexistente; usando padroes internos.", origem)
    return Config(dados, origem=origem)
