# Base própria de evidências e checagens

A base é um banco SQLite que guarda documentos rastreáveis (origem, data, trechos) e checagens anteriores. Na análise, a VeritAI busca nela evidências por BM25 e por vetores, e as checagens mais parecidas. A busca na web continua existindo, só para descobrir documentos.

- **Caminho:** `VERITAI_BASE`, com padrão `data/veritai.sqlite3`. A pasta `data/` não vai para o git.
- **Sem o arquivo do banco,** o serviço funciona como antes, só com web e evidências do publisher.

## Carga

```bash
.venv/bin/python -m veritai.base importar-urls fontes.txt            # uma URL por linha; # comenta
.venv/bin/python -m veritai.base importar-textos doc1.txt doc2.md --data 2026-03-01
.venv/bin/python -m veritai.base importar-checagens --arquivo claims.json
.venv/bin/python -m veritai.base importar-checagens --consulta "termos"   # exige GOOGLE_FACT_CHECK_API_KEY
.venv/bin/python -m veritai.base indexar --perfil v1
.venv/bin/python -m veritai.base estatisticas
.venv/bin/python -m veritai.base documento 12                     # origem de um documento citado no relatório
```

- **`importar-urls`:**
  - Lê uma página por vez, com a mesma checagem de URL pública e o mesmo limite de 1,5 MB da análise.
  - Aceita no máximo `base.max_urls_por_carga` URLs por arquivo.
  - Não é ferramenta de coleta em massa nem de raspagem: a lista de fontes confiáveis é definida pelo grupo.
- **Proibido:** nunca carregue as notícias fictícias da interface do FOMO.
- **`importar-checagens --arquivo`:** recebe um JSON no formato da resposta de `claims:search` do Google Fact Check, com a lista em `claims`.
- **Veredito das checagens:** o veredito é guardado como a agência escreveu (`textualRating`). O mapeamento para as 4 classes da VeritAI cabe à área de Dados e não é feito aqui.
- **Snapshot:** cada carga com conteúdo novo gera um identificador de snapshot (`base-AAAAMMDDTHHMMSSZ-xxxxxx`). O relatório o mostra em `versoes["base_evidencias"]`. Uma carga sem nada novo não gera snapshot.
- **Publicações repetidas:** cada publicação é um documento. O mesmo texto em outra URL ou em outro arquivo entra com a própria data e fica no mesmo grupo de cópias. Só a mesma referência com o mesmo texto conta como repetida.
- **Arquivos locais:** são identificados pelo caminho completo. Arquivos com o mesmo nome em pastas diferentes são fontes distintas.
  - No relatório, uma evidência da base sem URL aparece como `<nome> (base própria, documento N)`.
  - `python -m veritai.base documento N` mostra a origem do documento: caminho ou URL, datas, hash, grupo de cópias e carga. O caminho local não vai para o relatório.
- **Código de saída da linha de comando:**
  - `0`: carga sem erros;
  - `1`: nada entrou e houve erros;
  - `2`: carga parcial, em que algo entrou mas houve erros. Confira as mensagens `Erro:` antes de tratar o lote como completo.
- **Leitura de URLs:** cada host é resolvido uma única vez. Todos os endereços precisam ser públicos (IPv4 e IPv6), e a conexão vai para o IP já validado. Em HTTPS, o SNI e a verificação do certificado usam o nome original. Assim, um domínio não consegue trocar de endereço entre a checagem e a conexão. Redirecionamentos continuam recusados e proxies do ambiente são ignorados.

## Vetores por perfil

Os vetores são calculados com o embedding do perfil (`v0` ou `v1`) e guardados por nome de modelo. Se a base tiver trechos sem vetor para o embedding do perfil em uso, a busca falha com erro claro e a API não sobe. Isso cobre perfil trocado e documentos novos ainda não indexados.

A correção é rodar `python -m veritai.base indexar --perfil <perfil>`. Vetores de modelos diferentes nunca se misturam numa busca.

## Leitura consistente

O banco usa WAL. Cada análise abre uma única transação de leitura (`BaseEvidencias.leitura()`): o snapshot registrado, as buscas e as checagens de todas as afirmações vêm do mesmo estado. Uma carga gravada durante a análise só aparece na análise seguinte.

O esquema tem versão (`PRAGMA user_version`). Uma base com esquema diferente é recusada com a instrução de recriá-la.

## Busca

- **BM25:** usa o FTS5 do SQLite, com tokenizador `unicode61` sem acentos. Cada termo da afirmação vai entre aspas e os termos se combinam com `OR`.
- **Vetorial:** busca exata com numpy, por produto escalar de vetores normalizados. Os prefixos do e5 valem na V1. FAISS e pgvector ficam para depois.
- **Fusão:** Reciprocal Rank Fusion, com `base.rrf_k` = 60. Entram na análise os `base.trechos_por_busca` primeiros, como fontes de origem `base_propria`.
- **Data:** com `noticia.data` no pedido, documentos publicados depois desse dia ficam de fora já na busca. Documentos sem data entram.
- **Checagens:** as `base.checagens_por_afirmacao` checagens mais parecidas pelo BM25 sobre a alegação aparecem em `checagens_anteriores`, só como informação. Elas não definem o resultado.

## Cópias

Cada documento novo é comparado com os já carregados por shingles de `copias.palavras_por_shingle` palavras e similaridade de Jaccard. Com Jaccard ≥ `copias.jaccard_minimo`, ele entra no mesmo `grupo_copias`.

Na análise, o mesmo critério vale para todas as origens (base, web e publisher). O mesmo domínio ou o mesmo grupo de cópias conta como uma única fonte independente.

**Limitação:** só cópias quase literais são agrupadas. Matérias reescritas da mesma origem ainda contam como independentes. Em textos curtos, de poucas dezenas de palavras, uma única troca já pode baixar o Jaccard abaixo do corte. Os parâmetros são de operação e não foram validados.

## Esquema

| Tabela | Colunas | Observações |
|---|---|---|
| `cargas` | `id` (snapshot), `criada_em`, `comando`, `detalhes` | Uma linha por carga com conteúdo novo. |
| `documentos` | `id`, `url`, `referencia` (URL ou caminho do arquivo), `dominio`, `titulo`, `tipo_origem` (`url` ou `texto_local`), `data_publicacao` (AAAA-MM-DD ou nulo), `data_coleta`, `hash_conteudo` (SHA-256), `grupo_copias`, `texto`, `carga` | Único por `(referencia, hash_conteudo)`. `grupo_copias` é o `id` do primeiro documento do grupo. O texto inteiro é guardado para detectar cópias. |
| `trechos` | `id`, `documento`, `posicao`, `texto` | Cerca de `base.palavras_por_trecho` palavras, com cerca de `base.palavras_de_sobreposicao` de sobreposição, sem partir frases. |
| `trechos_fts` | `texto` | Índice FTS5 (conteúdo externo de `trechos`). |
| `vetores` | `trecho`, `embedding`, `dimensao`, `vetor` (float32) | Chave `(trecho, embedding)`. |
| `checagens` | `id`, `alegacao`, `veredito_original`, `agencia`, `data`, `url`, `carga` | Única por `(url, alegacao)`. |
| `checagens_fts` | `alegacao` | Índice FTS5 (conteúdo externo de `checagens`). |

## O que ainda não existe

- **Remoção e atualização de documentos:** a base hoje só cresce. Uma página que mudou entra como documento novo.
- **Detecção de cópias em escala:** cada documento novo é comparado com todos os outros, o que só serve para bases pequenas.
- **Índice vetorial aproximado:** FAISS e pgvector ainda não foram adotados. PostgreSQL também não.
