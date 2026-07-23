# GPX Analisador de Rotas

Aplicação para Windows que processa arquivos **GPX de câmeras GoPro** e
identifica, **na ordem correta**, as ruas, avenidas, rodovias e demais vias
pelas quais a câmera passou.

O programa lê os pontos de GPS, limpa e simplifica o trajeto, baixa **apenas as
vias necessárias** do OpenStreetMap (com cache local reutilizável), faz o
**map matching** do percurso sobre a malha viária (algoritmo HMM/Viterbi) e
gera uma **planilha Excel consolidada**, um **mapa HTML interativo**, um
**JSON técnico** e, opcionalmente, um **GeoPackage** por arquivo.

> Desenvolvido e validado com Python 3.14.6 no Windows 10 x64.
> Arquivo de teste: `GX152586_1_GPS5.gpx` (3.225 pontos, ~718 m, Manaus/AM).

---

## Sumário

- [Principais recursos](#principais-recursos)
- [Instalação no Windows](#instalação-no-windows)
- [Como usar (interface gráfica)](#como-usar-interface-gráfica)
- [Como usar (linha de comando)](#como-usar-linha-de-comando)
- [Configurações](#configurações)
- [Saídas geradas](#saídas-geradas)
- [Arquitetura](#arquitetura)
- [O algoritmo de associação às vias](#o-algoritmo-de-associação-às-vias)
- [Cache territorial e funcionamento offline](#cache-territorial-e-funcionamento-offline)
- [Testes](#testes)
- [Gerar o executável (.exe)](#gerar-o-executável-exe)
- [Limitações conhecidas](#limitações-conhecidas)
- [Recomendações de evolução](#recomendações-de-evolução)

---

## Principais recursos

- Seleciona **um arquivo GPX** ou **uma pasta** (com opção de **subpastas**).
- **Preserva os arquivos originais** — nunca os altera.
- Limpeza configurável: duplicados, coordenadas inválidas, saltos impossíveis e
  períodos parado.
- Simplificação inteligente combinando **amostragem por distância**,
  **Douglas-Peucker** e **preservação de curvas** (mantém os pontos originais,
  com horários).
- Download do OSM **somente nas áreas necessárias**, em **tiles de ~5 km**
  reutilizáveis; **não rebaixa** regiões já em cache.
- **Map matching sequencial (HMM/Viterbi)**: considera distância, direção,
  continuidade e conectividade da rede — evita alternar entre ruas paralelas.
- Agrupa vias na ordem percorrida, remove oscilações e **preserva retornos**
  reais à mesma via (A → B → A).
- Calcula por trecho: horário de entrada/saída, duração, distância, coordenadas,
  distância média/máxima até a via e **índice de confiança** (Alta/Média/Baixa/
  Não identificado).
- **Excel** consolidado, **mapa HTML** interativo, **JSON** técnico e
  **GeoPackage** opcional.
- Processamento **em lote**, que **continua mesmo se um arquivo falhar**.
- Interface **não trava** (processamento em thread), com **cancelamento seguro**.
- Banco **SQLite** para catálogo de cache e histórico de arquivos processados
  (detecção de reprocessamento por **hash de conteúdo**).

---

## Instalação no Windows

### Pré-requisitos

- **Python 3.11 ou superior** (validado com 3.14). Baixe em
  [python.org](https://www.python.org/downloads/windows/) e marque
  *"Add Python to PATH"* na instalação.

### 1. Obter o projeto

Copie a pasta `gpx_analisador_rotas` para o computador (ex.: no Desktop).

### 2. Criar o ambiente virtual

Abra o **PowerShell** dentro da pasta do projeto e execute:

```bash
py -m venv .venv
```

### 3. Ativar o ambiente virtual

```bash
.venv\Scripts\Activate.ps1
```

> Se o PowerShell bloquear a ativação, rode uma vez:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### 4. Instalar as dependências

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Todas as bibliotecas possuem *wheels* para Windows (nenhuma exige compilador).

---

## Como usar (interface gráfica)

Com o ambiente ativado:

```bash
python app.py
```

Passos na janela:

1. **Seleção de arquivos** — *Selecionar arquivo(s)* ou *Selecionar pasta*
   (marque *Subpastas* para varrer recursivamente). A lista mostra os arquivos
   encontrados e a contagem; é possível remover itens selecionados ou limpar.
2. **Saída** — escolha a pasta e quais formatos gerar (Excel, Mapa HTML,
   GeoPackage, JSON, Diagnóstico, abrir a pasta ao terminar).
3. **Configurações básicas** — margem de download, espaçamento da simplificação,
   raio para encontrar vias, validade do cache, *Somente cache (offline)* e
   *Atualizar ruas já baixadas*. Ajustes finos ficam em **Avançado**.
4. **Cache de vias** — ver o tamanho do cache ou limpá-lo.
5. **Execução** — *Processar* (roda em segundo plano), *Cancelar* (interrompe
   com segurança) e *Resetar*. Acompanhe as barras de progresso, os contadores
   (processados / erros / a conferir) e o painel de log.

## Como usar (linha de comando)

A CLI usa o mesmo núcleo (útil para automação e servidores sem interface):

```bash
# Inspecionar um GPX (trilhas, pontos, período, distância, campos, anomalias)
python run_cli.py "C:\videos\GX152586_1_GPS5.gpx" --inspecionar

# Processar um arquivo (Excel + mapa + JSON em ./output)
python run_cli.py "C:\videos\GX152586_1_GPS5.gpx"

# Processar uma pasta com subpastas, salvando em outra pasta
python run_cli.py "C:\videos" --recursivo --saida "C:\saida"

# Modo offline (usa apenas o cache) e também gerar GeoPackage e diagnóstico
python run_cli.py "C:\videos\x.gpx" --offline --geopackage --diagnostico
```

`python run_cli.py --help` lista todas as opções.

---

## Configurações

Editáveis em `config/configuracoes.json` (ou pela GUI). Principais valores
(distâncias em metros):

| Chave | Padrão | Descrição |
|---|---|---|
| `buffer_ruas_m` | 300 | Margem ao redor do percurso para escolher os tiles a baixar |
| `espacamento_pontos_m` | 10 | Espaçamento-alvo da simplificação |
| `tolerancia_douglas_peucker_m` | 3 | Tolerância do Douglas-Peucker |
| `angulo_preservacao_curva_graus` | 20 | Mudança de direção que força preservar o ponto (curva) |
| `raio_candidatos_m` | 30 | Raio para buscar vias candidatas por ponto |
| `distancia_maxima_aceitavel_m` | 50 | Raio máximo (fallback) para candidatas |
| `tile_tamanho_km` | 5 | Tamanho do tile do cache |
| `cache_validade_dias` | 90 | Prazo para considerar um tile desatualizado |
| `permanencia_minima_trecho_m` | 20 | Trechos mais curtos que isso são tratados como oscilação |
| `salto_velocidade_maxima_kmh` | 200 | Acima disso é considerado salto/spike de GPS |
| `matching.sigma_gps_m` | 12 | Desvio esperado do GPS (emissão do HMM) |
| `matching.beta_transicao_m` | 12 | Escala da transição do HMM |
| `modo_offline` | false | Não acessa a internet |
| `atualizar_cache` | false | Rebaixa tiles mesmo já em cache |

Os limiares de confiança ficam em `confianca` e os pesos do matching em
`matching`. Consulte `DECISOES_TECNICAS.md` para o significado detalhado.

---

## Saídas geradas

- **`Relatorio_Rotas_GPX_DDMMYYYY_HHMMSS.xlsx`** (consolidado, nunca sobrescreve):
  - *Resumo* — uma linha por arquivo.
  - *Ruas percorridas* — uma linha por trecho, com horários, distâncias,
    coordenadas e confiança (colorida).
  - *Diagnóstico dos pontos* — opcional, uma linha por ponto processado.
  - *Erros e alertas* — ocorrências por etapa.
- **`Mapa_<arquivo>.html`** — mapa interativo (folium/Leaflet) com camadas
  ativáveis: trajeto original, simplificado, ajustado (colorido por confiança),
  início/fim, trechos não identificados e pontos problemáticos. Inclui legenda
  e atribuição ao OpenStreetMap.
- **`<arquivo>.json`** — JSON técnico (metadados, configurações, estatísticas,
  vias percorridas, alertas, versão do programa e data das vias).
- **`Rota_<arquivo>.gpkg`** — opcional; camadas `trajeto_original`,
  `trajeto_limpo`, `trajeto_simplificado`, `trajeto_ajustado`,
  `trechos_identificados` e `pontos_diagnostico`.

---

## Arquitetura

O **núcleo de processamento** (`src/`) é totalmente separado das interfaces
(`app.py` — GUI; `run_cli.py` — CLI). Ambas consomem o mesmo núcleo.

```
gpx_analisador_rotas/
├── app.py                 # Interface grafica (customtkinter, com thread e cancelamento)
├── run_cli.py             # Interface de linha de comando (mesmo nucleo)
├── requirements.txt
├── gpx_rotas.spec         # Empacotamento PyInstaller
├── config/configuracoes.json
├── src/
│   ├── utils.py           # Caminhos, logging, matematica geografica, nomes, hash
│   ├── config_loader.py   # Carrega/valida/mescla as configuracoes
│   ├── models.py          # Dataclasses do pipeline
│   ├── coordinate_system.py  # Deteccao de zona UTM e transformacoes metricas
│   ├── gpx_reader.py      # Leitura/inspecao (gpxpy) + metricas derivadas
│   ├── track_cleaner.py   # Limpeza (duplicados/invalidos/saltos/parado)
│   ├── track_simplifier.py# Simplificacao (distancia + Douglas-Peucker + curvas)
│   ├── osm_cache.py       # Tiles territoriais determinísticos (~5 km)
│   ├── osm_downloader.py  # Download por tile (OSMnx) + uniao/cache + offline
│   ├── database.py        # SQLite (tiles, arquivos processados, erros)
│   ├── road_matcher.py    # Map matching HMM/Viterbi (nucleo)
│   ├── confidence.py      # Classificacao de confianca por trecho
│   ├── route_processor.py # Orquestracao por arquivo e em lote + JSON
│   ├── excel_exporter.py  # Planilha Excel (4 abas)
│   └── map_exporter.py    # Mapa HTML (folium) + GeoPackage
├── data/                  # cache_osm/  banco/  logs/  processados/  (criadas em runtime)
├── output/                # saidas geradas
└── tests/                 # testes automatizados (pytest)
```

Fluxo por arquivo: **ler → limpar → simplificar → (garantir vias no cache) →
map matching → agrupar em trechos → calcular estatísticas/confiança →
exportar**. Em lote, as áreas de todos os arquivos são unidas e os tiles são
garantidos **uma única vez** antes do processamento.

### Por que estas bibliotecas

A stack sugerida no enunciado foi adotada integralmente (todas com *wheels* para
Python 3.14/Windows): `gpxpy`, `pandas`, `geopandas`, `shapely`, `pyproj`,
`osmnx`, `networkx`, `openpyxl`, `folium`, `sqlite3`, `logging`,
`customtkinter`. Foram acrescentadas `pyogrio` (I/O do GeoPackage, backend
padrão do geopandas), `rtree`/`scipy` (índices espaciais) e `requests`
(dependência do OSMnx). Detalhes e justificativas em `DECISOES_TECNICAS.md`.

---

## O algoritmo de associação às vias

O componente central (`road_matcher.py`) **não** associa cada ponto à via mais
próxima isoladamente. Ele busca a **sequência de vias globalmente mais provável**
com um modelo **HMM resolvido por Viterbi**, no estilo *Newson & Krumm (2009)*:

1. **Candidatas por ponto** — para cada ponto simplificado, buscam-se as arestas
   (vias) dentro do `raio_candidatos_m` usando um índice espacial (STRtree),
   tudo em coordenadas métricas (UTM).
2. **Probabilidade de emissão** — quão bem o ponto "cai" sobre a via:
   combina a **distância perpendicular** (ruído lateral do GPS, modelado como
   gaussiana com `sigma_gps_m`) com a **diferença de direção** entre o veículo e
   a via, mais um pequeno **bônus pela classe** da via.
3. **Probabilidade de transição** — quão plausível é ir de uma candidata (ponto
   *t*) para outra (ponto *t+1*): compara a **distância sobre a rede** (menor
   caminho no grafo dirigido, respeitando conectividade e sentido) com a
   **distância em linha reta** entre os pontos. Transições impossíveis (sem
   caminho dentro de um corte) são fortemente penalizadas. Isso trata
   naturalmente marginais, alças, retornos, viadutos e ruas paralelas.
4. **Viterbi** encontra a sequência de máxima verossimilhança.
5. **Agrupamento e suavização** (`route_processor.py`) — pontos consecutivos na
   mesma via viram um trecho; trocas mais curtas que `permanencia_minima_trecho_m`
   são absorvidas (oscilação), mas **retornos reais** à mesma via são
   preservados.

A arquitetura isola esse componente: para trocar o algoritmo, basta
reimplementar `map_match()` mantendo a assinatura.

### Nomes das vias

Prioridade de identificação: `name` → `official_name` → `ref` → `alt_name` →
classe + OSM ID → *"Via sem nome"*. A normalização (sem acento, minúsculas,
abreviações) é usada **apenas para comparar/agrupar**; o nome original é sempre
preservado nos relatórios.

---

## Cache territorial e funcionamento offline

O território é dividido em **tiles determinísticos de ~5 km** (identificador
reversível derivado das coordenadas, ex.: `t5k_-1335_-68`). Cada tile é baixado
**uma vez** (malha `drive`), salvo em **GraphML** (e opcionalmente GeoPackage) e
catalogado no SQLite (limites, data, nº de vias, situação, erros). Em lote, as
áreas de todos os arquivos são unidas e só os tiles ausentes/vencidos são
baixados — **a mesma região nunca é baixada duas vezes**.

Quando todas as áreas necessárias já estão em cache, o processamento funciona
**sem internet** (marque *Somente cache (offline)*). Se faltar uma área e não
houver rede, o programa **não encerra abruptamente**: informa as regiões
ausentes, gera resultado parcial e permite nova tentativa depois.

---

## Testes

Com o ambiente ativado:

```bash
python -m pip install pytest
python -m pytest -q
```

Cobrem: leitura de GPX, cálculo de distância, duplicados, simplificação,
preservação de curvas, zona UTM, tiles, cache/SQLite, agrupamento, retorno à
mesma via, via sem nome, classificação de confiança, geração de Excel e
cancelamento — além de cenários simulados de **ruas paralelas**, **cruzamento**
e **ruído lateral de GPS** com uma malha viária sintética (offline).

O teste de integração ponta a ponta com o `GX152586_1_GPS5.gpx` está marcado
como `integracao` e roda offline quando o cache está pronto.

Veja o resultado da execução do arquivo de teste em `docs/RELATORIO_TESTES.md`.

---

## Gerar o executável (.exe)

**Valide primeiro pela fonte** (acima). Depois, com o ambiente ativado:

```bash
.venv\Scripts\pyinstaller.exe gpx_rotas.spec --noconfirm --clean
```

O resultado fica em `dist\GPX_Analisador_Rotas\GPX_Analisador_Rotas.exe`.
Distribua a **pasta inteira** `dist\GPX_Analisador_Rotas\`. O executável:

- **não exige Python instalado** na máquina do usuário;
- cria as pastas `data/` e `output/` **ao lado do .exe** em tempo de execução
  (sem caminhos absolutos do computador de desenvolvimento);
- lida com caminhos com espaços e acentos.

A `gpx_rotas.spec` coleta explicitamente os dados/módulos das bibliotecas
geoespaciais (`pyproj`, `pyogrio`, `geopandas`, `osmnx`, `shapely`, `rtree`,
`folium`).

> Dependência externa opcional: para gerar/inspecionar **GeoPackage**, tudo já
> vai embutido via `pyogrio` (GDAL). Não é necessário instalar nada além da pasta.

---

## Limitações conhecidas

- Duas vias **diferentes com o mesmo nome** percorridas em sequência imediata
  podem ser agrupadas como uma só (o agrupamento é por identidade de nome). Casos
  não adjacentes aparecem como trechos separados normalmente.
- O download de um tile baixa a malha do **tile inteiro (~5 km)**, não apenas o
  buffer do percurso — é intencional (reuso), mas o **primeiro** download de uma
  região urbana pode levar alguns segundos.
- Percursos que cruzam fronteiras de **zona UTM** usam a zona do centroide
  (distorção desprezível nas extensões típicas de vídeos; ver
  `DECISOES_TECNICAS.md`).
- A distância por trecho é atribuída ao longo do trajeto simplificado; a soma
  aproxima muito bem (não exatamente) a distância total.
- Túneis/viadutos sobrepostos podem exigir ajuste dos pesos do matching em casos
  extremos.

---

## Recomendações de evolução

- Ajustar `sigma_gps_m`/`beta_transicao_m` por `hdop` de cada ponto
  (qualidade real do sinal), já previsto na arquitetura.
- Paralelizar o processamento **por arquivo** em lotes grandes (mantendo o
  limite de downloads simultâneos ao OSM).
- Persistir os resultados completos no SQLite para reaproveitar sem reprocessar.
- Desambiguar vias de mesmo nome por conectividade/geometria no agrupamento.
- Exportar um relatório PDF por vídeo, se necessário.

---

© CATTER Engenharia — uso interno. Dados de vias © colaboradores do
**OpenStreetMap** (ODbL).
