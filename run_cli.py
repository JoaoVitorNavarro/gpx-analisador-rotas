"""Interface de linha de comando (headless) do GPX Analisador de Rotas.

Util para automacao, testes de integracao e processamento em servidores sem
interface grafica. A GUI (app.py) usa exatamente o mesmo nucleo.

Exemplos:
    # Inspecionar um GPX
    python run_cli.py --inspecionar "C:/videos/GX152586_1_GPS5.gpx"

    # Processar um arquivo
    python run_cli.py "C:/videos/GX152586_1_GPS5.gpx"

    # Processar uma pasta (com subpastas), salvando em outra pasta, offline
    python run_cli.py "C:/videos" --recursivo --saida "C:/saida" --offline

    # Gerar tambem GeoPackage e a aba de diagnostico
    python run_cli.py "C:/videos/x.gpx" --geopackage --diagnostico
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# permitir execucao direta (python run_cli.py) e como modulo
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config_loader, gpx_reader, utils  # noqa: E402
from src.database import Database  # noqa: E402
from src.route_processor import BatchProcessor, Reporter  # noqa: E402


def _inspecionar(caminhos: list[Path]) -> None:
    for c in caminhos:
        print(f"\n=== Inspecao: {c.name} ===")
        try:
            info = gpx_reader.inspecionar(c)
        except Exception as e:  # noqa: BLE001
            print(f"  ERRO ao ler: {e}")
            continue
        print(f"  Trilhas: {info.n_trilhas} | Segmentos: {info.n_segmentos} | "
              f"Pontos: {info.n_pontos}")
        print(f"  Periodo: {info.tempo_inicial} -> {info.tempo_final} "
              f"(duracao {utils.formatar_duracao(info.duracao_s)})")
        print(f"  Distancia total: {info.distancia_total_m:.1f} m")
        print(f"  Limites: lat [{info.lat_min}, {info.lat_max}] "
              f"lon [{info.lon_min}, {info.lon_max}]")
        print(f"  Campos: {', '.join(info.campos)}")
        print(f"  Tem horario: {info.tem_tempo}")
        print(f"  Anomalias: {'; '.join(info.anomalias) if info.anomalias else 'nenhuma'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analisa GPX de GoPro e identifica as vias percorridas.")
    parser.add_argument("entradas", nargs="+", help="Arquivo(s) .gpx ou pasta(s).")
    parser.add_argument("--recursivo", action="store_true",
                        help="Procurar .gpx tambem em subpastas.")
    parser.add_argument("--saida", default=None, help="Pasta de saida (padrao: ./output).")
    parser.add_argument("--config", default=None, help="Arquivo de configuracoes JSON.")
    parser.add_argument("--offline", action="store_true", help="Nao acessar a internet.")
    parser.add_argument("--atualizar-cache", action="store_true",
                        help="Rebaixar tiles mesmo se ja estiverem em cache.")
    parser.add_argument("--geopackage", action="store_true", help="Gerar GeoPackage.")
    parser.add_argument("--diagnostico", action="store_true",
                        help="Incluir a aba de diagnostico dos pontos no Excel.")
    parser.add_argument("--sem-excel", action="store_true", help="Nao gerar Excel.")
    parser.add_argument("--sem-mapa", action="store_true", help="Nao gerar mapa HTML.")
    parser.add_argument("--inspecionar", action="store_true",
                        help="Apenas inspecionar os GPX (nao processa).")
    args = parser.parse_args(argv)

    utils.ensure_dirs()
    utils.setup_logging()

    # coletar arquivos
    arquivos: list[Path] = []
    for entrada in args.entradas:
        arquivos.extend(gpx_reader.encontrar_gpx(entrada, recursivo=args.recursivo))
    arquivos = sorted(set(arquivos))
    if not arquivos:
        print("Nenhum arquivo .gpx encontrado.")
        return 2
    print(f"{len(arquivos)} arquivo(s) .gpx encontrado(s).")

    if args.inspecionar:
        _inspecionar(arquivos)
        return 0

    cfg = config_loader.carregar_config(args.config)
    if args.offline:
        cfg.set("modo_offline", True)
    if args.atualizar_cache:
        cfg.set("atualizar_cache", True)
    if args.geopackage:
        cfg.set("saidas.gerar_geopackage", True)
    if args.diagnostico:
        cfg.set("gerar_diagnostico_pontos", True)
    if args.sem_excel:
        cfg.set("saidas.gerar_excel", False)
    if args.sem_mapa:
        cfg.set("saidas.gerar_mapa_html", False)

    saida = Path(args.saida) if args.saida else utils.default_output_dir()

    rep = Reporter(
        log=lambda m: print(m),
        prog_geral=lambda a, t: print(f"  progresso: {a}/{t}") if t else None,
    )
    db = Database()
    try:
        bp = BatchProcessor(cfg, db, rep)
        resultados = bp.processar(arquivos, saida)
    finally:
        db.fechar()

    print("\n=== RESUMO ===")
    ok = sum(1 for r in resultados if r.status == "ok")
    parcial = sum(1 for r in resultados if r.status == "parcial")
    erro = sum(1 for r in resultados if r.status == "erro")
    print(f"Processados: {len(resultados)} | ok: {ok} | parcial: {parcial} | erro: {erro}")
    for r in resultados:
        vias = " > ".join(t.nome_via for t in r.trechos) if r.trechos else "(nenhuma)"
        print(f"  [{r.status}] {r.nome}: {len(r.trechos)} trecho(s): {vias[:120]}")
    print(f"\nSaidas em: {saida}")
    return 0 if erro == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
