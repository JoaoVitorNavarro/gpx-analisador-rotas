"""Exportacao do mapa HTML interativo (folium) e do GeoPackage por arquivo.

O mapa mostra, em camadas ativaveis/desativaveis: o percurso original, o
simplificado, o percurso ajustado as vias (colorido por confianca), inicio/fim,
os trechos identificados (com nome, ordem e confianca no popup) e os pontos
problematicos (nao identificados). Inclui legenda e atribuicao ao OpenStreetMap.
Funciona localmente no navegador (arquivo unico .html).
"""
from __future__ import annotations

from pathlib import Path

import folium

from . import utils
from .config_loader import Config
from .models import (CONF_ALTA, CONF_BAIXA, CONF_MEDIA, CONF_NAO_IDENT, FileResult)

logger = utils.get_logger()

_COR_CONF = {
    CONF_ALTA: "#2E9B4F",
    CONF_MEDIA: "#E8A22B",
    CONF_BAIXA: "#E8552B",
    CONF_NAO_IDENT: "#8E44AD",
}
_COR_ORIGINAL = "#7F8C8D"
_COR_SIMPL = "#2C6FBB"

_LEGENDA_HTML = """
<div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999;
     background: white; padding: 12px 14px; border: 1px solid #bbb;
     border-radius: 6px; font: 12px/1.4 Arial, sans-serif; box-shadow: 0 1px 6px rgba(0,0,0,.3);">
  <b>Legenda</b><br>
  <span style="color:#7F8C8D;">&#9473;&#9473;</span> Trajeto original<br>
  <span style="color:#2C6FBB;">&#9473;&#9473;</span> Trajeto simplificado<br>
  <span style="color:#2E9B4F;">&#9473;&#9473;</span> Ajustado &mdash; confianca alta<br>
  <span style="color:#E8A22B;">&#9473;&#9473;</span> Ajustado &mdash; confianca media<br>
  <span style="color:#E8552B;">&#9473;&#9473;</span> Ajustado &mdash; confianca baixa<br>
  <span style="color:#8E44AD;">&#9473;&#9473;</span> Nao identificado<br>
</div>
"""


def _centro(fr: FileResult) -> tuple[float, float]:
    pts = fr.pontos_simplificados or fr.pontos_limpos or fr.pontos_originais
    lat = sum(p.lat for p in pts) / len(pts)
    lon = sum(p.lon for p in pts) / len(pts)
    return lat, lon


def exportar(fr: FileResult, cfg: Config, saida_dir: Path) -> Path:
    saida_dir = Path(saida_dir)
    saida_dir.mkdir(parents=True, exist_ok=True)
    centro = _centro(fr)

    m = folium.Map(location=centro, zoom_start=16, control_scale=True,
                   tiles="OpenStreetMap")

    # --- trajeto original ---
    if fr.pontos_originais:
        fg = folium.FeatureGroup(name="Trajeto original", show=False)
        folium.PolyLine([(p.lat, p.lon) for p in fr.pontos_originais],
                        color=_COR_ORIGINAL, weight=2, opacity=0.6).add_to(fg)
        fg.add_to(m)

    # --- trajeto simplificado ---
    if fr.pontos_simplificados:
        fg = folium.FeatureGroup(name="Trajeto simplificado", show=False)
        folium.PolyLine([(p.lat, p.lon) for p in fr.pontos_simplificados],
                        color=_COR_SIMPL, weight=3, opacity=0.7, dash_array="5,5").add_to(fg)
        fg.add_to(m)

    # --- trajeto ajustado / trechos ---
    fg_aj = folium.FeatureGroup(name="Trajeto ajustado as vias", show=True)
    fg_ni = folium.FeatureGroup(name="Trechos nao identificados", show=True)
    for t in fr.trechos:
        coords = [(lat, lon) for lon, lat in t.geometria_ajustada]
        if len(coords) < 2:
            continue
        cor = _COR_CONF.get(t.confianca, _COR_SIMPL)
        popup = folium.Popup(_popup_trecho(t), max_width=320)
        linha = folium.PolyLine(coords, color=cor, weight=6, opacity=0.85,
                                popup=popup, tooltip=f"{t.ordem}. {t.nome_via}")
        if t.confianca == CONF_NAO_IDENT:
            linha.add_to(fg_ni)
        else:
            linha.add_to(fg_aj)
    fg_aj.add_to(m)
    fg_ni.add_to(m)

    # --- pontos problematicos ---
    fg_prob = folium.FeatureGroup(name="Pontos problematicos", show=False)
    algum_prob = False
    for mp in fr.matched:
        if mp.escolhido is None:
            algum_prob = True
            folium.CircleMarker((mp.tp.lat, mp.tp.lon), radius=4, color="#C0392B",
                                fill=True, fill_opacity=0.9,
                                tooltip=f"Sem via: {mp.motivo_descarte}").add_to(fg_prob)
    if algum_prob:
        fg_prob.add_to(m)

    # --- inicio / fim ---
    fg_if = folium.FeatureGroup(name="Inicio / Fim", show=True)
    pts = fr.pontos_simplificados
    if pts:
        folium.Marker((pts[0].lat, pts[0].lon), tooltip="Inicio",
                      icon=folium.Icon(color="green", icon="play")).add_to(fg_if)
        folium.Marker((pts[-1].lat, pts[-1].lon), tooltip="Fim",
                      icon=folium.Icon(color="red", icon="stop")).add_to(fg_if)
    fg_if.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(_LEGENDA_HTML))
    titulo = f"<h4 style='font-family:Arial'>Rota: {fr.nome} ({fr.id_video})</h4>"
    m.get_root().html.add_child(folium.Element(
        f"<div style='position:fixed;top:8px;left:50px;z-index:9999;"
        f"background:white;padding:4px 10px;border-radius:4px;"
        f"box-shadow:0 1px 4px rgba(0,0,0,.3)'>{titulo}</div>"))

    destino = utils.caminho_unico(saida_dir / f"Mapa_{utils.nome_seguro(Path(fr.nome).stem)}.html")
    m.save(str(destino))
    logger.info("Mapa HTML gerado: %s", destino)
    return destino


def _popup_trecho(t) -> str:
    he = t.hora_entrada.strftime("%H:%M:%S") if t.hora_entrada else "-"
    hs = t.hora_saida.strftime("%H:%M:%S") if t.hora_saida else "-"
    return (
        f"<b>{t.ordem}. {t.nome_via}</b><br>"
        f"{'Ref.: ' + t.ref + '<br>' if t.ref else ''}"
        f"Classe: {t.classe or '-'}<br>"
        f"OSM ID: {t.osm_id if t.osm_id is not None else '-'}<br>"
        f"Entrada: {he} &nbsp; Saida: {hs}<br>"
        f"Duracao: {utils.formatar_duracao(t.duracao_s)}<br>"
        f"Distancia: {t.distancia_m:.0f} m &nbsp; Pontos: {t.n_pontos}<br>"
        f"Dist. media a via: {t.dist_media_m:.1f} m (max {t.dist_max_m:.1f} m)<br>"
        f"<b>Confianca: {t.confianca}</b>"
        f"{'<br>Obs.: ' + t.observacoes if t.observacoes else ''}"
    )


# ---------------------------------------------------------------------------
# GeoPackage
# ---------------------------------------------------------------------------


def exportar_geopackage(fr: FileResult, saida_dir: Path) -> Path:
    """Gera um GeoPackage com as camadas do processamento (EPSG:4326)."""
    import geopandas as gpd
    from shapely.geometry import LineString, Point

    saida_dir = Path(saida_dir)
    destino = utils.caminho_unico(
        saida_dir / f"Rota_{utils.nome_seguro(Path(fr.nome).stem)}.gpkg")

    camadas: dict[str, gpd.GeoDataFrame] = {}

    def _linha(pts):
        if len(pts) >= 2:
            return LineString([(p.lon, p.lat) for p in pts])
        return None

    for nome, pts in (("trajeto_original", fr.pontos_originais),
                      ("trajeto_limpo", fr.pontos_limpos),
                      ("trajeto_simplificado", fr.pontos_simplificados)):
        geom = _linha(pts)
        if geom is not None:
            camadas[nome] = gpd.GeoDataFrame(
                {"arquivo": [fr.nome], "n_pontos": [len(pts)]},
                geometry=[geom], crs="EPSG:4326")

    # trajeto ajustado (uniao das geometrias por trecho)
    coords_aj = [c for t in fr.trechos for c in t.geometria_ajustada]
    if len(coords_aj) >= 2:
        camadas["trajeto_ajustado"] = gpd.GeoDataFrame(
            {"arquivo": [fr.nome]}, geometry=[LineString(coords_aj)], crs="EPSG:4326")

    # trechos identificados
    linhas, attrs = [], []
    for t in fr.trechos:
        if len(t.geometria_ajustada) >= 2:
            linhas.append(LineString(t.geometria_ajustada))
            attrs.append({
                "ordem": t.ordem, "nome_via": t.nome_via, "ref": t.ref,
                "classe": t.classe, "osm_id": str(t.osm_id), "confianca": t.confianca,
                "dist_m": round(t.distancia_m, 1), "n_pontos": t.n_pontos,
            })
    if linhas:
        gdf = gpd.GeoDataFrame(attrs, geometry=linhas, crs="EPSG:4326")
        camadas["trechos_identificados"] = gdf

    # pontos de diagnostico
    if fr.matched:
        pts, attrs = [], []
        for mp in fr.matched:
            pts.append(Point(mp.tp.lon, mp.tp.lat))
            attrs.append({
                "ordem": mp.indice,
                "via": mp.escolhido.nome if mp.escolhido else "",
                "dist_via_m": round(mp.dist_via_m, 2) if mp.dist_via_m is not None else None,
                "vel_kmh": round(mp.tp.vel_kmh, 1) if mp.tp.vel_kmh is not None else None,
            })
        camadas["pontos_diagnostico"] = gpd.GeoDataFrame(attrs, geometry=pts, crs="EPSG:4326")

    if not camadas:
        raise ValueError("Nada a exportar para GeoPackage.")
    for nome, gdf in camadas.items():
        gdf.to_file(destino, layer=nome, driver="GPKG")
    logger.info("GeoPackage gerado: %s", destino)
    return destino
