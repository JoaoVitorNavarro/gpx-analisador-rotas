"""Banco de dados SQLite: controle de tiles OSM e arquivos processados.

Guarda o catalogo do cache territorial (quais tiles ja foram baixados, quando,
com quantas vias, situacao e erros) e o historico de arquivos processados
(hash do conteudo + hash das configuracoes) para detectar reprocessamento.

Thread-safe o suficiente para o uso do app (uma conexao por instancia, com
lock e check_same_thread=False), pois o processamento roda em uma thread
separada da interface.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from . import utils

logger = utils.get_logger()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS tiles (
    tile_id      TEXT PRIMARY KEY,
    lon_min      REAL, lat_min REAL, lon_max REAL, lat_max REAL,
    tile_km      REAL,
    data_download TEXT,
    n_vias       INTEGER,
    arquivo      TEXT,
    versao_base  TEXT,
    situacao     TEXT,          -- 'ok' | 'vazio' | 'erro' | 'pendente'
    erro         TEXT
);

CREATE TABLE IF NOT EXISTS arquivos_processados (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    caminho       TEXT,
    nome          TEXT,
    hash          TEXT,
    config_hash   TEXT,
    data_processamento TEXT,
    config_json   TEXT,
    status        TEXT,
    n_pontos      INTEGER,
    n_trechos     INTEGER,
    distancia_m   REAL,
    resumo_json   TEXT,
    versao_programa TEXT
);

CREATE INDEX IF NOT EXISTS idx_arq_hash ON arquivos_processados(hash, config_hash);

CREATE TABLE IF NOT EXISTS erros_download (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tile_id  TEXT,
    data     TEXT,
    mensagem TEXT
);
"""


def agora_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


class Database:
    def __init__(self, caminho: str | Path | None = None):
        self.caminho = Path(caminho) if caminho else (utils.db_dir() / "gpx_rotas.sqlite")
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.caminho), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(ESQUEMA)
        self._con.commit()
        logger.info("Banco SQLite pronto em %s", self.caminho)

    def fechar(self) -> None:
        with self._lock:
            try:
                self._con.commit()
                self._con.close()
            except sqlite3.Error:
                pass

    # ------------------- tiles -------------------
    def registrar_tile(self, tile_id: str, bounds: tuple[float, float, float, float],
                       tile_km: float, n_vias: int, arquivo: str, situacao: str,
                       versao_base: str = "", erro: str = "") -> None:
        with self._lock:
            self._con.execute(
                """INSERT INTO tiles(tile_id, lon_min, lat_min, lon_max, lat_max,
                       tile_km, data_download, n_vias, arquivo, versao_base, situacao, erro)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tile_id) DO UPDATE SET
                       lon_min=excluded.lon_min, lat_min=excluded.lat_min,
                       lon_max=excluded.lon_max, lat_max=excluded.lat_max,
                       tile_km=excluded.tile_km, data_download=excluded.data_download,
                       n_vias=excluded.n_vias, arquivo=excluded.arquivo,
                       versao_base=excluded.versao_base, situacao=excluded.situacao,
                       erro=excluded.erro""",
                (tile_id, bounds[0], bounds[1], bounds[2], bounds[3], tile_km,
                 agora_iso(), n_vias, arquivo, versao_base, situacao, erro),
            )
            self._con.commit()

    def obter_tile(self, tile_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._con.execute("SELECT * FROM tiles WHERE tile_id=?", (tile_id,))
            return cur.fetchone()

    def listar_tiles(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._con.execute("SELECT * FROM tiles ORDER BY tile_id"))

    def tile_valido(self, tile_id: str, validade_dias: int) -> bool:
        """True se o tile esta em cache, com situacao utilizavel e dentro do prazo."""
        row = self.obter_tile(tile_id)
        if row is None or row["situacao"] not in ("ok", "vazio"):
            return False
        if not row["data_download"]:
            return False
        try:
            dt = _dt.datetime.fromisoformat(row["data_download"])
        except ValueError:
            return False
        idade = (_dt.datetime.now() - dt).days
        return idade <= validade_dias

    def registrar_erro_download(self, tile_id: str, mensagem: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO erros_download(tile_id, data, mensagem) VALUES(?,?,?)",
                (tile_id, agora_iso(), mensagem[:1000]),
            )
            self._con.commit()

    def limpar_tiles(self) -> int:
        with self._lock:
            cur = self._con.execute("SELECT COUNT(*) c FROM tiles")
            n = cur.fetchone()["c"]
            self._con.execute("DELETE FROM tiles")
            self._con.commit()
            return n

    # ------------------- arquivos processados -------------------
    def buscar_resultado(self, hash_arquivo: str, config_hash: str) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._con.execute(
                """SELECT * FROM arquivos_processados
                   WHERE hash=? AND config_hash=? ORDER BY id DESC LIMIT 1""",
                (hash_arquivo, config_hash),
            )
            return cur.fetchone()

    def registrar_arquivo(self, *, caminho: str, nome: str, hash_arquivo: str,
                          config_hash: str, config: dict[str, Any], status: str,
                          n_pontos: int, n_trechos: int, distancia_m: float,
                          resumo: dict[str, Any], versao_programa: str) -> int:
        with self._lock:
            cur = self._con.execute(
                """INSERT INTO arquivos_processados(
                       caminho, nome, hash, config_hash, data_processamento,
                       config_json, status, n_pontos, n_trechos, distancia_m,
                       resumo_json, versao_programa)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (caminho, nome, hash_arquivo, config_hash, agora_iso(),
                 json.dumps(config, ensure_ascii=False), status, n_pontos,
                 n_trechos, distancia_m, json.dumps(resumo, ensure_ascii=False),
                 versao_programa),
            )
            self._con.commit()
            return int(cur.lastrowid)

    # ------------------- utilitario -------------------
    def tamanho_cache_bytes(self) -> int:
        """Soma o tamanho dos arquivos de grafo/gpkg referenciados no cache."""
        total = 0
        for row in self.listar_tiles():
            arq = row["arquivo"]
            if arq and Path(arq).exists():
                total += Path(arq).stat().st_size
        return total
