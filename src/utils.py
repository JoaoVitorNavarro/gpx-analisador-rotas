"""Utilitarios gerais: caminhos, logging, matematica geografica, nomes e hashes.

Este modulo nao depende de nenhum outro modulo do projeto para evitar ciclos.
Funciona tanto executando pelo codigo-fonte quanto empacotado com PyInstaller.
"""
from __future__ import annotations

import hashlib
import logging
import logging.handlers
import math
import os
import re
import sys
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Caminhos (compativel com execucao por codigo-fonte e com PyInstaller)
# ---------------------------------------------------------------------------


def is_frozen() -> bool:
    """True quando executando dentro de um executavel PyInstaller."""
    return getattr(sys, "frozen", False)


def app_base_dir() -> Path:
    """Diretorio-base gravavel da aplicacao.

    - Executando pelo codigo-fonte: raiz do projeto (pasta que contem 'src').
    - Empacotado (PyInstaller): pasta onde esta o .exe (dados ficam ao lado
      do executavel, nunca dentro do bundle temporario read-only).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # utils.py fica em <raiz>/src/utils.py -> raiz = parents[1]
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    """Pasta de recursos empacotados (somente leitura no modo frozen)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_base_dir()))
    return app_base_dir()


def data_dir() -> Path:
    d = app_base_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = data_dir() / "cache_osm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_dir() -> Path:
    d = data_dir() / "banco"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_output_dir() -> Path:
    d = app_base_dir() / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_config_path() -> Path:
    """Localiza o configuracoes.json.

    Prioridade: pasta 'config' ao lado do exe/raiz -> recurso empacotado.
    """
    candidate = app_base_dir() / "config" / "configuracoes.json"
    if candidate.exists():
        return candidate
    return bundle_dir() / "config" / "configuracoes.json"


def ensure_dirs() -> None:
    """Cria toda a arvore de dados necessaria (idempotente)."""
    for d in (data_dir(), cache_dir(), db_dir(), logs_dir(), default_output_dir(),
              data_dir() / "processados"):
        Path(d).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Logging com rotacao
# ---------------------------------------------------------------------------

_LOG_CONFIGURED = False


def setup_logging(nivel: int = logging.INFO, nome_arquivo: str = "gpx_rotas.log") -> logging.Logger:
    """Configura logging com rotacao de arquivos. Idempotente."""
    global _LOG_CONFIGURED
    logger = logging.getLogger("gpx_rotas")
    if _LOG_CONFIGURED:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.handlers.RotatingFileHandler(
        logs_dir() / nome_arquivo, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(nivel)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    _LOG_CONFIGURED = True
    return logger


def get_logger(nome: str = "gpx_rotas") -> logging.Logger:
    return logging.getLogger(nome)


@contextmanager
def cronometro(logger: logging.Logger, etapa: str) -> Iterator[None]:
    """Mede e registra o tempo de uma etapa."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        logger.info("Etapa '%s' levou %.3fs", etapa, dt)


# ---------------------------------------------------------------------------
# Matematica geografica (WGS84) - uso rapido; calculos precisos usam pyproj/UTM
# ---------------------------------------------------------------------------

RAIO_TERRA_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia em metros entre dois pontos WGS84 (formula de haversine)."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * RAIO_TERRA_M * math.asin(min(1.0, math.sqrt(a)))


def azimute_graus(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Azimute (bearing) de (lat1,lon1) para (lat2,lon2), 0-360 graus (0=Norte)."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def diff_angular(a: float, b: float) -> float:
    """Menor diferenca entre dois angulos em graus (0-180)."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def diff_angular_orientacao(a: float, b: float) -> float:
    """Diferenca considerando via de mao dupla (0-90); ignora sentido oposto."""
    d = diff_angular(a, b)
    return min(d, 180.0 - d)


def coordenada_valida(lat: float, lon: float) -> bool:
    if lat is None or lon is None:
        return False
    if math.isnan(lat) or math.isnan(lon):
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    # (0,0) e um artefato classico de GPS sem fixacao
    if abs(lat) < 1e-7 and abs(lon) < 1e-7:
        return False
    return True


# ---------------------------------------------------------------------------
# Normalizacao de nomes de vias (apenas para comparacao/duplicidade)
# ---------------------------------------------------------------------------

_ABREVIACOES = {
    "r": "rua",
    "av": "avenida",
    "avn": "avenida",
    "al": "alameda",
    "tv": "travessa",
    "trav": "travessa",
    "pc": "praca",
    "pca": "praca",
    "rod": "rodovia",
    "estr": "estrada",
    "est": "estrada",
    "prof": "professor",
    "profa": "professora",
    "dr": "doutor",
    "dra": "doutora",
    "eng": "engenheiro",
    "sen": "senador",
    "pe": "padre",
    "sta": "santa",
    "sto": "santo",
    "s": "sao",
}


def remover_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar_nome_via(nome: str | None) -> str:
    """Normaliza um nome de via APENAS para comparacao/deteccao de duplicidade.

    Remove acentos, baixa a caixa, expande abreviacoes comuns e colapsa espacos.
    O nome original NUNCA e alterado nos relatorios; use somente para comparar.
    """
    if not nome:
        return ""
    t = remover_acentos(nome).lower()
    t = re.sub(r"[^\w\s-]", " ", t)
    t = re.sub(r"[-_]", " ", t)
    palavras = [p for p in t.split() if p]
    palavras = [_ABREVIACOES.get(p, p) for p in palavras]
    return " ".join(palavras).strip()


# ---------------------------------------------------------------------------
# Arquivos: hash e nomes seguros/unicos
# ---------------------------------------------------------------------------


def hash_arquivo(caminho: str | os.PathLike, algoritmo: str = "sha256") -> str:
    """Calcula o hash do conteudo do arquivo (identidade independente do nome)."""
    h = hashlib.new(algoritmo)
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def nome_seguro(nome: str) -> str:
    """Remove caracteres invalidos para nomes de arquivo no Windows."""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome).strip().rstrip(".")
    return base or "arquivo"


def caminho_unico(destino: Path) -> Path:
    """Retorna um caminho que ainda nao existe (evita sobrescrever)."""
    destino = Path(destino)
    if not destino.exists():
        return destino
    base = destino.stem
    ext = destino.suffix
    pai = destino.parent
    i = 1
    while True:
        cand = pai / f"{base}_{i}{ext}"
        if not cand.exists():
            return cand
        i += 1


def formatar_duracao(segundos: float | None) -> str:
    """Formata segundos como HH:MM:SS (ou '-' se None)."""
    if segundos is None:
        return "-"
    segundos = int(round(segundos))
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
