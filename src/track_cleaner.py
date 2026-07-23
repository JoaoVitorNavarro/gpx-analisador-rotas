"""Limpeza configuravel do trajeto.

Remove/atenua problemas comuns de GPS SEM apagar grandes trechos em silencio:
  - pontos exatamente duplicados (ou dentro de uma distancia minima);
  - coordenadas invalidas remanescentes;
  - saltos incompativeis com deslocamento rodoviario (spikes isolados);
  - periodos parado (afina o aglomerado para nao gerar falsas trocas de rua).

Os horarios originais sao preservados. Cada remocao e contabilizada por motivo
e registrada no log. O arquivo GPX original nunca e modificado.
"""
from __future__ import annotations

from . import gpx_reader, utils
from .config_loader import Config
from .models import CleanStats, TrackPoint

logger = utils.get_logger()


def limpar(pontos: list[TrackPoint], cfg: Config) -> tuple[list[TrackPoint], CleanStats]:
    """Aplica a limpeza e retorna (pontos_limpos, estatisticas)."""
    stats = CleanStats(n_entrada=len(pontos))
    if not pontos:
        stats.n_saida = 0
        return [], stats

    dist_dup = float(cfg.get("limpeza.distancia_duplicado_m", 0.5))
    hdop_max = float(cfg.get("limpeza.hdop_maximo", 30.0))
    vel_max = float(cfg.get("salto_velocidade_maxima_kmh", 200.0))
    parado_vel = float(cfg.get("limpeza.parado_velocidade_kmh", 1.5))
    parado_min = int(cfg.get("limpeza.parado_janela_min_pontos", 5))

    # copia rasa (nao mutar a lista original de pontos originais)
    trabalho = list(pontos)

    trabalho = _remover_invalidos(trabalho, hdop_max, stats)
    trabalho = _remover_duplicados(trabalho, dist_dup, stats)
    gpx_reader.recomputar_metricas(trabalho)
    trabalho = _remover_saltos(trabalho, vel_max, stats)
    gpx_reader.recomputar_metricas(trabalho)
    trabalho = _afinar_parado(trabalho, parado_vel, parado_min, stats)
    gpx_reader.recomputar_metricas(trabalho)

    stats.n_saida = len(trabalho)
    logger.info(
        "Limpeza: %d -> %d pontos (dup=%d, inval=%d, salto=%d, parado=%d)",
        stats.n_entrada, stats.n_saida, stats.removidos_duplicados,
        stats.removidos_invalidos, stats.removidos_salto, stats.removidos_parado,
    )
    return trabalho, stats


def _remover_invalidos(pontos, hdop_max, stats) -> list[TrackPoint]:
    saida = []
    for p in pontos:
        if not utils.coordenada_valida(p.lat, p.lon):
            stats.removidos_invalidos += 1
            continue
        if p.hdop is not None and p.hdop > hdop_max:
            stats.removidos_invalidos += 1
            stats.detalhes.append(f"ponto {p.indice}: hdop {p.hdop} > {hdop_max}")
            continue
        saida.append(p)
    return saida


def _remover_duplicados(pontos, dist_dup, stats) -> list[TrackPoint]:
    if not pontos:
        return pontos
    saida = [pontos[0]]
    for p in pontos[1:]:
        ant = saida[-1]
        d = utils.haversine_m(ant.lat, ant.lon, p.lat, p.lon)
        mesmo_tempo = (ant.tempo is not None and p.tempo is not None
                       and ant.tempo == p.tempo)
        if d <= dist_dup and not mesmo_tempo:
            # mesmo lugar em instante posterior: e "parado", tratado adiante;
            # so descartamos como duplicado se praticamente coincidente
            if d <= 0.05:
                stats.removidos_duplicados += 1
                continue
        if mesmo_tempo and d <= dist_dup:
            stats.removidos_duplicados += 1
            continue
        saida.append(p)
    return saida


def _remover_saltos(pontos, vel_max, stats) -> list[TrackPoint]:
    """Remove spikes isolados: ponto com velocidade de entrada E saida absurdas.

    Um unico ponto que "pula" para longe e retorna gera velocidade alta na
    entrada e na saida; e removido. Movimento rapido porem sustentado (varios
    pontos coerentes) e preservado, apenas registrado como aviso.
    """
    if len(pontos) < 3:
        return pontos
    manter = [True] * len(pontos)
    removidos_idx = []
    for i in range(1, len(pontos) - 1):
        p_prev, p, p_next = pontos[i - 1], pontos[i], pontos[i + 1]
        v_in = p.vel_kmh
        # velocidade de saida (prev=p): calcular direto
        v_out = None
        if p.tempo is not None and p_next.tempo is not None:
            dt = (p_next.tempo - p.tempo).total_seconds()
            if dt > 0:
                d = utils.haversine_m(p.lat, p.lon, p_next.lat, p_next.lon)
                v_out = (d / dt) * 3.6
        if v_in is not None and v_out is not None and v_in > vel_max and v_out > vel_max:
            manter[i] = False
            removidos_idx.append(p.indice)
    saida = [p for keep, p in zip(manter, pontos) if keep]
    n = len(removidos_idx)
    if n:
        stats.removidos_salto += n
        stats.detalhes.append(f"saltos removidos (spikes) nos indices originais: {removidos_idx[:20]}"
                              + (" ..." if n > 20 else ""))
    return saida


def _afinar_parado(pontos, parado_vel, parado_min, stats) -> list[TrackPoint]:
    """Afina aglomerados de pontos parados para evitar falsas trocas de rua.

    Um "aglomerado parado" e uma sequencia de pontos com velocidade abaixo do
    limiar. Preservamos o PRIMEIRO e o ULTIMO ponto do aglomerado (mantendo
    horarios de entrada e saida) e descartamos os intermediarios apenas quando
    o aglomerado for maior que a janela minima.
    """
    if len(pontos) < 3:
        return pontos
    saida: list[TrackPoint] = []
    i = 0
    n = len(pontos)
    while i < n:
        p = pontos[i]
        parado = (p.vel_kmh is not None and p.vel_kmh < parado_vel)
        if not parado:
            saida.append(p)
            i += 1
            continue
        # inicio de aglomerado parado; achar o fim
        j = i
        while j + 1 < n and pontos[j + 1].vel_kmh is not None \
                and pontos[j + 1].vel_kmh < parado_vel:
            j += 1
        tamanho = j - i + 1
        if tamanho >= parado_min:
            saida.append(pontos[i])       # entrada do aglomerado
            if j != i:
                saida.append(pontos[j])   # saida do aglomerado
            stats.removidos_parado += (tamanho - (2 if j != i else 1))
        else:
            saida.extend(pontos[i:j + 1])
        i = j + 1
    return saida
