"""Download e carga da malha viaria do OpenStreetMap por tiles (via OSMnx).

Estrategia de lote:
  1. As areas necessarias (bbox + buffer) de TODOS os arquivos ja foram
     convertidas em tiles determinísticos (osm_cache).
  2. Aqui verificamos quais tiles ja estao em cache e validos.
  3. Baixamos SOMENTE os tiles ausentes/desatualizados (um de cada vez, com
     pausa, para nao sobrecarregar os servidores publicos do Overpass).
  4. Cada tile e salvo em GraphML (e opcionalmente GeoPackage) e reutilizado.

Modo offline: nao acessa a rede; tiles ausentes sao apenas reportados como
faltantes, permitindo resultado parcial.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import networkx as nx
import osmnx as ox
from osmnx._errors import InsufficientResponseError, ResponseStatusCodeError
from shapely.geometry import box

from . import utils
from .config_loader import Config
from .database import Database
from .osm_cache import Tile

logger = utils.get_logger()

_OSMNX_CONFIGURADO = False


def configurar_osmnx(cfg: Config) -> None:
    """Configura settings globais do OSMnx (idempotente)."""
    global _OSMNX_CONFIGURADO
    if _OSMNX_CONFIGURADO:
        return
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(utils.cache_dir() / "osmnx_http")
    ox.settings.log_console = False
    ox.settings.requests_timeout = int(cfg.get("osm.overpass_timeout_s", 180))
    ox.settings.overpass_rate_limit = True
    _OSMNX_CONFIGURADO = True
    logger.info("OSMnx configurado (cache em %s)", ox.settings.cache_folder)


@dataclass
class ResultadoTiles:
    ok: list[str] = field(default_factory=list)        # tiles utilizaveis (com vias)
    vazios: list[str] = field(default_factory=list)    # tiles sem vias
    baixados: list[str] = field(default_factory=list)  # baixados nesta execucao
    reutilizados: list[str] = field(default_factory=list)
    faltando: list[str] = field(default_factory=list)  # ausentes (offline/sem rede)
    erros: list[str] = field(default_factory=list)

    @property
    def usaveis(self) -> list[str]:
        return self.ok


def garantir_tiles(db: Database, tiles: list[Tile], cfg: Config,
                   cancelado=lambda: False, progresso=None) -> ResultadoTiles:
    """Garante que os tiles estejam disponiveis, baixando os que faltam."""
    res = ResultadoTiles()
    offline = bool(cfg.get("modo_offline", False))
    atualizar = bool(cfg.get("atualizar_cache", False))
    validade = int(cfg.get("cache_validade_dias", 90))
    network_type = str(cfg.get("osm.network_type", "drive"))
    pausa = float(cfg.get("osm.pausa_entre_downloads_s", 1.0))
    tamanho_km = float(cfg.get("tile_tamanho_km", 5))

    if not offline:
        configurar_osmnx(cfg)

    total = len(tiles)
    for i, tile in enumerate(tiles):
        if cancelado():
            logger.info("Download de tiles cancelado pelo usuario.")
            break

        row = db.obter_tile(tile.id)
        valido = db.tile_valido(tile.id, validade) and not atualizar
        arquivo_existe = (tile.caminho_grafo().exists() if row and row["situacao"] == "ok"
                          else True)

        if valido and (row["situacao"] == "vazio" or arquivo_existe):
            res.reutilizados.append(tile.id)
            if row["situacao"] == "ok":
                res.ok.append(tile.id)
            else:
                res.vazios.append(tile.id)
            _prog(progresso, f"Tile {tile.id} reutilizado do cache", i + 1, total)
            continue

        if offline:
            res.faltando.append(tile.id)
            _prog(progresso, f"Tile {tile.id} ausente (modo offline)", i + 1, total)
            continue

        # baixar
        _prog(progresso, f"Baixando tile {tile.id} do OpenStreetMap...", i + 1, total)
        try:
            n_vias = _baixar_tile(tile, network_type, cfg)
            if n_vias > 0:
                db.registrar_tile(tile.id, tile.bounds, tamanho_km, n_vias,
                                  str(tile.caminho_grafo()), "ok",
                                  versao_base=_hoje())
                res.ok.append(tile.id)
                res.baixados.append(tile.id)
            else:
                db.registrar_tile(tile.id, tile.bounds, tamanho_km, 0, "", "vazio",
                                  versao_base=_hoje())
                res.vazios.append(tile.id)
            logger.info("Tile %s baixado: %d vias", tile.id, n_vias)
        except InsufficientResponseError:
            db.registrar_tile(tile.id, tile.bounds, tamanho_km, 0, "", "vazio",
                              versao_base=_hoje())
            res.vazios.append(tile.id)
            logger.info("Tile %s sem vias (resposta vazia).", tile.id)
        except (ResponseStatusCodeError, OSError, ValueError, ConnectionError) as e:
            db.registrar_tile(tile.id, tile.bounds, tamanho_km, 0, "", "erro",
                              erro=str(e))
            db.registrar_erro_download(tile.id, str(e))
            res.erros.append(tile.id)
            logger.error("Erro ao baixar tile %s: %s", tile.id, e)
        except Exception as e:  # noqa: BLE001 - registrar e seguir (nao travar o lote)
            db.registrar_tile(tile.id, tile.bounds, tamanho_km, 0, "", "erro",
                              erro=repr(e))
            db.registrar_erro_download(tile.id, repr(e))
            res.erros.append(tile.id)
            logger.exception("Falha inesperada no tile %s", tile.id)

        if pausa > 0 and i < total - 1:
            time.sleep(pausa)

    logger.info("Tiles: %d ok, %d vazios, %d baixados, %d reutilizados, %d faltando, %d erros",
                len(res.ok), len(res.vazios), len(res.baixados),
                len(res.reutilizados), len(res.faltando), len(res.erros))
    return res


def _baixar_tile(tile: Tile, network_type: str, cfg: Config) -> int:
    """Baixa a malha viaria do tile e salva em GraphML/GeoPackage. Retorna n_vias."""
    lo0, la0, lo1, la1 = tile.bounds
    poligono = box(lo0, la0, lo1, la1)
    G = ox.graph_from_polygon(
        poligono, network_type=network_type,
        retain_all=True, truncate_by_edge=True, simplify=True,
    )
    n_vias = G.number_of_edges()
    if n_vias == 0:
        return 0
    ox.save_graphml(G, filepath=str(tile.caminho_grafo()))
    if bool(cfg.get("saidas.gerar_geopackage", False)):
        try:
            ox.io.save_graph_geopackage(G, filepath=str(tile.caminho_gpkg()))
        except Exception as e:  # noqa: BLE001 - gpkg e opcional
            logger.warning("Nao foi possivel salvar gpkg do tile %s: %s", tile.id, e)
    return n_vias


def carregar_grafo(tiles: list[Tile], db: Database):
    """Carrega e une (compose) os grafos dos tiles utilizaveis. Retorna grafo ou None."""
    grafos = []
    for tile in tiles:
        row = db.obter_tile(tile.id)
        if not row or row["situacao"] != "ok":
            continue
        caminho = tile.caminho_grafo()
        if not caminho.exists():
            logger.warning("GraphML ausente para tile %s (%s)", tile.id, caminho)
            continue
        try:
            grafos.append(ox.load_graphml(filepath=str(caminho)))
        except Exception as e:  # noqa: BLE001
            logger.error("Falha ao carregar grafo do tile %s: %s", tile.id, e)
    if not grafos:
        return None
    if len(grafos) == 1:
        return grafos[0]
    G = nx.compose_all(grafos)
    logger.info("Grafo unido: %d nos, %d arestas (%d tiles)",
                G.number_of_nodes(), G.number_of_edges(), len(grafos))
    return G


def _prog(progresso, msg, atual, total):
    if progresso:
        progresso(msg, atual, total)


def _hoje() -> str:
    import datetime as _dt
    return _dt.date.today().isoformat()
