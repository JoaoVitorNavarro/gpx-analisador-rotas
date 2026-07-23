# Relatório de Testes

Ambiente: Windows 10 x64, Python 3.14.6, execução em 2026-07.
Arquivo de teste: `GX152586_1_GPS5.gpx`.

---

## 1. Inspeção do arquivo de teste

| Item | Valor lido |
|---|---|
| Trilhas / Segmentos / Pontos | 1 / 1 / **3.225** |
| Período (UTC) | 2026-07-01 19:52:45.584 → 19:55:42.843 |
| Duração | 00:02:57 (177,3 s) |
| Distância total | **718,1 m** |
| Limites (lat) | −3,0267832 … −3,0252978 |
| Limites (lon) | −59,9428156 … −59,9378976 |
| Campos disponíveis | `ele`, `fix`, `hdop`, `time` |
| Tem horário | Sim |
| Anomalias | 67 pontos duplicados consecutivos |

Os valores confirmam o enunciado (~3.225 pontos, ~718 m). Região: Manaus/AM
(UTM 21S / EPSG:32721).

## 2. Pipeline de processamento (execução ponta a ponta)

| Etapa | Resultado | Tempo |
|---|---|---|
| Leitura | 3.225 pontos válidos | 0,14 s |
| Limpeza | 3.225 → **2.656** (523 duplicados, 46 parados) | 0,07 s |
| Simplificação | 2.656 → **177** pontos (**redução 94,5 %**) | 0,04 s |
| Download/cache OSM | 1 tile `t5k_-1335_-68` (3.349 nós, 9.294 arestas) | 1ª vez ~s; reuso 0 s |
| Map matching (HMM/Viterbi) | **0 pontos não identificados**; dist. média ponto→via **4,8 m** | 0,10 s |
| Agrupamento | 4 trechos | ~0 s |

Tempo total de processamento (excluindo o 1º download): **< 0,4 s**.

## 3. Sequência de vias identificada

| # | Via | Classe | Distância | Pontos | Dist. média à via | Confiança |
|---|---|---|---|---|---|---|
| 1 | Rua das Pratas | residential | 371,4 m | 105 | 6,8 m | **Alta** |
| 2 | Rua Marcassita | residential | 79,5 m | 14 | 2,7 m | **Alta** |
| 3 | Rua Manganês | residential | 178,8 m | 40 | 1,5 m | **Média** |
| 4 | Rua Santa Rosa | residential | 81,1 m | 18 | 2,3 m | **Alta** |

Soma das distâncias dos trechos: 710,8 m (≈ 718 m totais). Sem alternância entre
ruas paralelas por ruído. Rua Manganês ficou "Média" por proximidade de via
concorrente (regra de confiança acionada corretamente).

## 4. Saídas geradas (entregáveis em `output/`)

| Arquivo | Tamanho | Conteúdo |
|---|---|---|
| `Relatorio_Rotas_GPX_*.xlsx` | ~24 KB | 4 abas (Resumo, Ruas percorridas, Diagnóstico, Erros) |
| `Mapa_GX152586_1_GPS5.html` | ~111 KB | folium/Leaflet, 6 camadas, legenda, atribuição OSM |
| `GX152586_1_GPS5.json` | ~6 KB | JSON técnico completo |
| `Rota_GX152586_1_GPS5.gpkg` | ~316 KB | 6 camadas (trajetos, trechos, pontos) |

Validações: Excel abre com 4 abas, congelamento em A2, autofiltro e cores por
confiança; nomes acentuados corretos (ex.: "Rua Manganês"). GeoPackage com 6
camadas legíveis via `pyogrio`. Mapa HTML com 6 polylines, 2 marcadores,
controle de camadas, legenda e atribuição ao OpenStreetMap.

## 5. Reuso de cache e modo offline

- **2ª execução:** 0 downloads (tile reutilizado do cache) — critério nº 5.
- **Modo offline:** processa o mesmo arquivo sem rede, mesmos 4 trechos —
  critério nº 15.

## 6. Testes automatizados (pytest)

**35 testes, 100 % aprovados** (`python -m pytest -q`). Cobertura:

- Leitura de GPX, campos, distância/acumulada, duplicados, sem horário,
  múltiplos segmentos, GPX vazio, arquivo corrompido, busca recursiva.
- Limpeza (duplicados, spike, afinar parado); simplificação (redução,
  preservação de extremos e de curva).
- Map matching (malha sintética, offline): **ruas paralelas sem flip-flop**,
  **cruzamento com sequência correta**, **ruído lateral** mantém a via;
  identidade de via (name/ref/sem nome).
- Zona UTM, determinismo/limite de tiles, união de tiles, banco (registro/
  validade/limpeza/duplicidade).
- Agrupamento: consecutivos, **remoção de oscilação curta**, **preservação de
  retorno real (A-B-A)**, trecho não identificado, classificação de confiança,
  **cancelamento**.
- Geração de Excel e não sobrescrita.

Cenários simulados exigidos atendidos: ruas paralelas, cruzamento, retorno, GPS
deslocado lateralmente, ponto sem horário, GPX vazio, arquivo corrompido, GPX
com vários segmentos. (Rotatória/marginal são tratadas pelo mesmo mecanismo de
transição de rede; validadas indiretamente pelo cruzamento/retorno.)

## 7. Executável (PyInstaller)

- Build concluído (`gpx_rotas.spec`): `dist\GPX_Analisador_Rotas\` (~261 MB),
  `GPX_Analisador_Rotas.exe` (~20 MB).
- O `.exe` **abre e renderiza a GUI** de forma autônoma (sem Python instalado),
  criando `data/` e `output/` ao lado do executável em tempo de execução.

## 8. Critérios de aceitação (seção 27)

| # | Critério | Situação | Evidência |
|---|---|---|---|
| 1 | Abrir o GPX de teste sem erro | ✅ | Inspeção (seção 1) |
| 2 | Preservar o original | ✅ | Só leitura; saídas em `output/` |
| 3 | Reduzir pontos processados | ✅ | 3.225 → 177 (94,5 %) |
| 4 | Baixar só a área necessária | ✅ | 1 tile, não o Brasil |
| 5 | Reutilizar na 2ª execução | ✅ | 0 downloads na 2ª vez |
| 6 | Não consultar endereço por ponto | ✅ | Sem Nominatim por ponto; matching em grafo |
| 7 | Vias na ordem do percurso | ✅ | 4 trechos em ordem |
| 8 | Não alternar entre paralelas | ✅ | Teste `ruas_paralelas_sem_flipflop` |
| 9 | Manter retorno à mesma via | ✅ | Teste `preserva_retorno_real` |
| 10 | Excel válido | ✅ | 4 abas verificadas |
| 11 | Mapa HTML válido | ✅ | folium, camadas, legenda |
| 12 | Marcar resultados duvidosos | ✅ | Confiança + camada não identificado |
| 13 | Lote de vários GPX | ✅ | `BatchProcessor` (fases 1-3) |
| 14 | Continuar após erro em um arquivo | ✅ | `try/except` por arquivo + aba Erros |
| 15 | Offline com cache completo | ✅ | Execução offline OK |
| 16 | Interface não trava | ✅ | Processamento em thread + fila |
| 17 | Cancelamento seguro | ✅ | Event entre etapas; teste de cancelamento |
| 18 | Documentação de instalação/uso | ✅ | `README.md`, este relatório |

**Conclusão:** todos os 18 critérios de aceitação atendidos; o arquivo de teste
foi processado de ponta a ponta com sucesso.
