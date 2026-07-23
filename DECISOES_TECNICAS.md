# Decisões Técnicas

Registro das decisões relevantes tomadas no projeto e suas justificativas.

## 1. Compatibilidade com Python 3.14

O ambiente-alvo tinha **apenas Python 3.14.6**. Antes de definir a arquitetura,
todas as bibliotecas da stack sugerida foram testadas nesse interpretador
(Windows x64) e **todas possuem wheels** — nenhuma exige compilador C. Versões
validadas estão em `requirements.txt` (shapely 2.1.2, pyproj 3.7.2,
geopandas 1.1.4, osmnx 2.1.1, networkx 3.6.1, customtkinter 6.0.0, etc.).

## 2. Bibliotecas adicionadas à stack sugerida

- **pyogrio** — backend de I/O do geopandas (leitura/escrita de GeoPackage via
  GDAL, já embutido nas wheels). Mais rápido e simples que fiona.
- **rtree** / **scipy** — índices espaciais usados por shapely/geopandas.
- **requests** — dependência do OSMnx para acessar o Overpass.

Todas as sugeridas foram mantidas. Nenhuma foi substituída.

## 3. Download por tile inteiro (não apenas o buffer do percurso)

O enunciado pede tanto um **buffer de 300 m** ao redor do percurso (seção 10)
quanto um **cache territorial em tiles de ~5 km** (seção 11). Essas duas ideias
foram conciliadas assim:

- O **buffer** decide **quais tiles** são "tocados" pelo percurso.
- Cada **tile** é a unidade de cache e é baixado **por inteiro** (os ~5 km),
  não apenas a faixa do buffer.

**Motivo:** baixar o tile inteiro é o que torna o cache **reutilizável** — um
próximo vídeo que passe em outra rua do mesmo tile aproveita os dados já
baixados, sem novo acesso ao OSM. Baixar só o buffer economizaria no primeiro
download, mas quebraria o reuso (a maioria dos vídeos futuros exigiria novos
downloads). O custo é um primeiro download um pouco maior por tile (segundos),
pago **uma única vez**.

## 4. Identificador de tile determinístico e reversível

Formato: `t{km}k_{ix}_{iy}`, com `ix = floor(lon/passo)`,
`iy = floor(lat/passo)` e `passo = tamanho_km/111,32` (graus). É determinístico
(mesma coordenada → mesmo tile), **reversível** (dá para reconstruir os limites)
e suporta índices negativos (hemisfério sul / oeste). Ex.: `t5k_-1335_-68`.

## 5. Zona UTM pelo centroide (percursos multi-zona)

As distâncias são calculadas em **UTM** (metros). A zona é escolhida pelo
**centroide** do percurso. Quando um percurso cruza a fronteira de zonas, é
registrado um aviso e mantém-se a zona do centroide: para as extensões típicas
de vídeos de GoPro (poucos km), a distorção de escala do UTM fora da zona
central é **desprezível** (< 0,1 % até ~200 km do meridiano central). Para
percursos futuros muito extensos, o módulo `coordinate_system` já oferece uma
alternativa documentada: **projeção azimutal equidistante** local
(`transformer_local_aeqd`), que preserva distâncias a partir do centro.

## 6. Map matching HMM/Viterbi (não vizinho mais próximo)

Optou-se **diretamente** pelo modelo **HMM resolvido por Viterbi** (estilo
Newson & Krumm, 2009), como preferido no enunciado, em vez do fallback baseado
apenas em candidatos/suavização. Justificativa: é o que garante a **prioridade
nº 1** (correção da sequência das vias), tratando ruas paralelas, marginais,
retornos e cruzamentos de forma global, não ponto a ponto.

- **Emissão:** distância perpendicular (gaussiana, `sigma_gps_m`) + diferença
  de direção veículo × via + bônus por classe.
- **Transição:** compara a distância **sobre a rede** (menor caminho no grafo
  dirigido, respeitando sentido/conectividade, com corte) à distância em linha
  reta; penaliza transições impossíveis.
- O componente é **isolado** atrás de `map_match()` — trocá-lo não afeta o resto.

Desempenho: para o arquivo de teste (177 pontos simplificados), o matching leva
~0,1 s. A distância de rede usa Dijkstra **com corte** e **cache**, mantendo o
custo baixo mesmo em percursos maiores.

## 7. Simplificação preservando pontos originais

A simplificação **seleciona pontos originais** (não interpola posições novas).
Assim os **horários/altitude/hdop** originais são preservados, o que é essencial
para calcular horário de entrada/saída por via. Combina três critérios unidos:
amostragem por distância (≤ ~10 m), Douglas-Peucker (formato/curvas) e âncoras
de curva/velocidade. Isso satisfaz o "considerar distância física" sem usar a
regra ingênua de "1 a cada N registros".

## 8. Identidade da via por nome normalizado

O agrupamento usa uma **identidade** da via: `name` → `official_name` → `ref` →
`alt_name` → classe+OSM ID → "Via sem nome". A normalização (sem acento,
minúsculas, abreviações expandidas) serve **apenas para comparar**; o nome
original é sempre preservado na saída. Limitação: duas vias diferentes com o
mesmo nome, percorridas em sequência imediata, poderiam ser unidas — aceitável
para o caso de uso e registrado em "Limitações".

## 9. Suavização por distância (permanência mínima)

A remoção de oscilações usa **distância percorrida**, não contagem de pontos:
trechos menores que `permanencia_minima_trecho_m` (padrão 20 m) entre a mesma
via (A-b-A) ou entre vias diferentes são absorvidos; trechos acima do limiar
— inclusive **retornos reais** (A-B-A) — são preservados. Isso atende
diretamente à seção 14 do enunciado.

## 10. Confiança por regras explícitas

A classificação (Alta/Média/Baixa/Não identificado) segue **regras claras**
sobre métricas objetivas (distância média/máxima à via, diferença de direção,
nº de pontos, margem para a 2ª candidata), com todos os limiares em
`config/configuracoes.json` (seção `confianca`). Não há percentuais arbitrários.

## 11. Threading e cancelamento seguro

O processamento roda em **thread separada**; a comunicação com a GUI é por uma
**fila** (a interface nunca é tocada pela thread de trabalho). O cancelamento é
um `threading.Event` verificado **entre etapas seguras** (entre arquivos e entre
downloads de tiles); o SQLite faz commit por operação, então cancelar não
corrompe o banco nem arquivos.

## 12. Caminhos compatíveis com PyInstaller

`utils.app_base_dir()` distingue execução por código-fonte (raiz do projeto) e
empacotada (pasta do `.exe`). As pastas `data/`, `output/`, `cache_osm/`,
`banco/`, `logs/` são criadas **em tempo de execução ao lado do executável** —
sem caminhos absolutos do computador de desenvolvimento. Caminhos com espaços e
acentos são tratados via `pathlib`.

## 13. Ajuste de parâmetros durante os testes

Os valores iniciais do enunciado foram mantidos por funcionarem bem no arquivo
de teste (0 pontos não identificados, distância média ponto→via de 4,8 m).
Parâmetros de matching que **não** constavam no enunciado foram adicionados com
padrões conservadores em `matching` (sigma 12 m, beta 12 m, raio de candidatos
reaproveita `raio_candidatos_m`). Nenhum valor do enunciado foi alterado.
