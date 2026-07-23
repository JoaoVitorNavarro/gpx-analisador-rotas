# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/);
este projeto adota versionamento semântico (SemVer).

## [1.0.0] — 2026-07-23

### Adicionado
- Leitura e inspeção de GPX (gpxpy): múltiplas trilhas/segmentos, campos
  `ele`/`time`/`hdop`/`fix`, distância, distância acumulada, velocidade e
  azimute; detecção de duplicados, coordenadas inválidas e saltos.
- Limpeza configurável do trajeto (duplicados, inválidos, spikes, parado).
- Simplificação inteligente (amostragem por distância + Douglas-Peucker +
  preservação de curvas), mantendo os pontos originais.
- Sistema de coordenadas com detecção automática de zona UTM.
- Cache territorial em tiles determinísticos de ~5 km (GraphML + SQLite),
  download por tile via OSMnx, união de áreas em lote e funcionamento offline.
- **Map matching sequencial HMM/Viterbi** (emissão por distância/direção,
  transição por distância de rede/conectividade) — núcleo do sistema.
- Agrupamento em trechos com suavização de oscilações e preservação de
  retornos reais (A→B→A).
- Índice de confiança por regras explícitas (Alta/Média/Baixa/Não identificado).
- Saídas: Excel consolidado (4 abas), mapa HTML interativo (folium),
  JSON técnico e GeoPackage.
- Interface gráfica (customtkinter) com processamento em thread e cancelamento
  seguro; interface de linha de comando (`run_cli.py`).
- Banco SQLite (catálogo de tiles + histórico por hash de conteúdo).
- 35 testes automatizados (pytest) e empacotamento com PyInstaller.

### Notas
- Validado com Python 3.14.6 no Windows 10 x64.
- Arquivo de teste `GX152586_1_GPS5.gpx` processado de ponta a ponta
  (4 vias identificadas, 0 pontos não identificados).
