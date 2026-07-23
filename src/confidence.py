"""Classificacao de confianca por trecho.

Nao usa percentuais arbitrarios: a classificacao segue regras explicitas sobre
metricas objetivas do trecho, com todos os limiares centralizados no arquivo de
configuracao (secao 'confianca'):

  - distancia media e maxima dos pontos ate a via (ajuste geometrico);
  - diferenca media de direcao entre veiculo e via;
  - quantidade de pontos que sustentam o trecho;
  - margem para a 2a melhor candidata (ambiguidade / vias concorrentes);
  - se a via foi realmente identificada.

Niveis: Alta, Media, Baixa, Nao identificado.
"""
from __future__ import annotations

from .config_loader import Config
from .models import (CONF_ALTA, CONF_BAIXA, CONF_MEDIA, CONF_NAO_IDENT, Trecho)


def classificar(trecho: Trecho, azdiff_medio: float | None,
                margem_media: float | None, cfg: Config) -> str:
    """Classifica o trecho e acrescenta observacoes explicando o rebaixamento."""
    if not trecho.road_id:
        return CONF_NAO_IDENT

    c = cfg.get("confianca", {})
    dm_alta = float(c.get("dist_media_alta_m", 8.0))
    dm_media = float(c.get("dist_media_media_m", 18.0))
    dmax_alta = float(c.get("dist_max_alta_m", 20.0))
    az_alta = float(c.get("dif_azimute_alta_graus", 25.0))
    az_media = float(c.get("dif_azimute_media_graus", 55.0))
    pts_min = int(c.get("pontos_minimos_confiavel", 3))
    margem_alta = float(c.get("margem_candidato_alta", 1.5))

    obs: list[str] = []
    az = azdiff_medio if azdiff_medio is not None else 0.0
    margem = margem_media if margem_media is not None else margem_alta

    cond_alta = (
        trecho.dist_media_m <= dm_alta
        and trecho.dist_max_m <= dmax_alta
        and az <= az_alta
        and trecho.n_pontos >= pts_min
        and margem >= margem_alta
    )
    cond_media = (
        trecho.dist_media_m <= dm_media
        and az <= az_media
        and trecho.n_pontos >= 2
    )

    if cond_alta:
        nivel = CONF_ALTA
    elif cond_media:
        nivel = CONF_MEDIA
        if trecho.dist_media_m > dm_alta:
            obs.append("distancia media elevada")
        if az > az_alta:
            obs.append("direcao divergente")
        if margem < margem_alta:
            obs.append("via concorrente proxima")
    else:
        nivel = CONF_BAIXA
        if trecho.dist_media_m > dm_media:
            obs.append(f"pontos distantes da via (media {trecho.dist_media_m:.0f} m)")
        if az > az_media:
            obs.append("direcao muito divergente")
        if trecho.n_pontos < 2:
            obs.append("trecho muito curto (poucos pontos)")

    if obs:
        extra = "; ".join(obs)
        trecho.observacoes = (trecho.observacoes + " | " + extra).strip(" |") \
            if trecho.observacoes else extra
    return nivel
