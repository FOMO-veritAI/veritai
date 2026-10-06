# VeritAI

Serviço de IA do **FOMO** (*Fear of Missing Objectivity*) que analisa afirmações factuais de uma notícia antes da publicação e devolve um relatório por afirmação, com evidências, fontes e limitações. O nome une *veritas* (“a verdade”, em latim) e *AI*.

A VeritAI recomenda e documenta. A decisão de publicar é sempre de um revisor humano, na plataforma FOMO ([FOMO-veritAI/fomo](https://github.com/FOMO-veritAI/fomo)).

## Estado atual

| Componente | Situação |
|---|---|
| API `POST /analisar` com relatório por afirmação | Implementado |
| Perfil V0: similaridade (MiniLM) + NLI (MiniLMv2) + regras | Implementado, perfil padrão; mesmos modelos da V0, com a seleção nova de janelas |
| Baseline V1: similaridade (e5-base) + NLI (mDeBERTa-v3) + regras | Implementado como perfil `v1`, ainda não medido em dados rotulados |
| Janelas de 1 a 2 frases; 3 janelas por fonte levadas ao NLI | Implementado |
| Script de avaliação (`eval/avaliar.py`) e conjunto de contraste (só sanidade) | Implementado |
| Busca de fontes: Bing News RSS, GDELT, Google Fact Check (opcional) | Implementado |
| Evidências enviadas pelo publisher (links e documentos com texto) | Implementado |
| Contagem de fontes independentes por domínio | Implementado |
| Conjunto rotulado real e medição V0 × V1 | Planejado |
| Base própria em SQLite: documentos, trechos, checagens, busca BM25 + vetorial com RRF | Implementado, ainda sem a carga das fontes do grupo |
| Fontes independentes por grupo (mesmo domínio ou cópia quase literal) | Implementado |
| Filtro de evidências pela data da notícia | Implementado |
| Leitura de URLs com resolução de DNS única (conexão no IP validado, TLS pelo nome original) | Implementado |
| Imagem de container (Dockerfile, docker-compose) | Implementado, validado localmente em arm64 |
| Imagem oficial para nuvem (GitHub Actions), Kubernetes e AWS | Planejado |
| PostgreSQL + pgvector, atualização de documentos na base | Planejado |
| Modelo treinado V2 (XLM-RoBERTa ajustado) | Planejado, depende de dados rotulados |
| Porcentagem calibrada | Planejado, depende de treino, teste e calibração |

**Hoje não existe porcentagem.** O campo `porcentagem` vem sempre `null` e `avaliavel` vem `false`, com o motivo registrado em `motivo_nao_avaliavel`. Os limiares em `veritai/config.json` são herdados do protótipo V0 e não foram validados para nenhum perfil. Eles terão de ser definidos por perfil, na avaliação com dados rotulados. Na V1, o filtro de similaridade atual praticamente não atua. Em observação exploratória no conjunto de contraste, o e5 deu entre 0,80 e 0,87 a todos os pares, acima dos limiares de 0,38 e 0,43 (detalhes em [`eval/README.md`](eval/README.md)).

## Perfis de modelo

`veritai/config.json` define os perfis `v0` e `v1`. O perfil é escolhido pela variável de ambiente `VERITAI_PERFIL`, com padrão `v0` até a V1 ser medida em dados rotulados. O campo `versoes` do relatório e o `GET /health` mostram o perfil em uso.

```bash
VERITAI_PERFIL=v1 .venv/bin/python -m uvicorn veritai.api:app --host 127.0.0.1 --port 8100
```

Para cada afirmação, o texto das fontes é dividido em janelas de uma e de duas frases. As 3 janelas mais similares de cada fonte, em até 6 fontes, vão para o NLI. A seleção dá prioridade a domínios diferentes antes de repetir páginas do mesmo site. Esses números são parâmetros de operação, em `selecao` no config. De cada fonte, entram no relatório a janela com maior probabilidade de apoio e a com maior probabilidade de contradição.

## Como rodar

Requer Python 3.11 ou mais recente.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[modelos,dev]"
.venv/bin/python -m uvicorn veritai.api:app --host 127.0.0.1 --port 8100
```

Documentação interativa: `http://127.0.0.1:8100/docs`. Na primeira análise, os modelos são baixados do Hugging Face.

Para usar o Google Fact Check, copie `.env.example` para `.env` e preencha a chave. O `.env` nunca vai para o git.

## Testes

```bash
.venv/bin/python -m pytest
docker build --target teste .        # a mesma suíte com Python 3.12, a versão da imagem
```

Os testes usam modelos e busca simulados: rodam em menos de um segundo e não precisam do extra `modelos`. O estágio `teste` do Dockerfile não entra na imagem final.

## Demonstração técnica

```bash
.venv/bin/python demo/gerar_demo.py      # precisa do extra modelos; grava demo/saida/demo.json (fora do git)
```

O arquivo traz três casos fictícios (Exemplópolis) passados pelos perfis v0 e v1, com o rastro de cada etapa: trechos, janelas, similaridade, janelas levadas ao NLI, probabilidades, limiares, regra de agregação, grupos de fontes, relatório da API e tempos. Também traz a avaliação no conjunto de contraste (só sanidade), o resumo da suíte de testes e os metadados (commit e versões). O rastro é interno, um parâmetro opcional do comparador, e não altera a saída da API.

## Container

A imagem roda a API na porta 8100, com Python 3.12, torch só-CPU e usuário sem privilégios (uid/gid 10001). Modelos e base ficam fora da imagem, em volumes.

| Caminho ou variável | Para quê |
|---|---|
| `/cache` (volume, `HF_HOME`) | Modelos do Hugging Face. São baixados na primeira análise e reaproveitados depois. |
| `/data` (volume, `VERITAI_BASE=/data/veritai.sqlite3`) | Base própria SQLite. Sem o arquivo, o serviço usa só web e publisher. |
| `VERITAI_PERFIL` | Perfil de modelos (`v0`, padrão, ou `v1`). |
| `GOOGLE_FACT_CHECK_API_KEY` | Opcional. Entra só na execução, nunca na imagem. |

**Com Docker Compose (local):**

```bash
docker compose up -d --build                 # constrói e liga; VERITAI_PERFIL=v1 docker compose up -d para a V1
docker compose ps                            # estado e health check
curl http://127.0.0.1:8100/health
docker compose logs -f veritai               # logs (Ctrl+C para sair)
docker compose down                          # desliga (os volumes continuam)
docker compose down -v                       # desliga e apaga os volumes de modelos e base
```

A porta é publicada só em `127.0.0.1`. A API não tem autenticação, e qualquer cliente que a alcance pode disparar inferência e buscas externas, então não exponha a porta na rede sem uma camada de autenticação na frente.

O compose lê `VERITAI_PERFIL` e `GOOGLE_FACT_CHECK_API_KEY` do shell ou do `.env` local só para preencher as variáveis do container. O `.env` não é copiado para a imagem: ele está no `.dockerignore`, e o Dockerfile copia só `pyproject.toml`, `README.md` e `veritai/`. O container roda com sistema de arquivos só leitura e escreve apenas nos volumes e em `/tmp`.

**Só com Docker:**

```bash
docker build -t veritai:local .
docker run -d --name veritai -p 127.0.0.1:8100:8100 \
  -e VERITAI_PERFIL=v0 \
  -v veritai-hf-cache:/cache -v veritai-data:/data \
  --read-only --tmpfs /tmp \
  veritai:local
docker stop veritai && docker rm veritai
```

**Linha de comando da base dentro do container:**

```bash
docker compose run --rm -v "$PWD/documentos:/entrada:ro" veritai python -m veritai.base importar-textos /entrada/relatorio.txt --data 2026-03-01
docker compose run --rm veritai python -m veritai.base indexar --perfil v0
docker compose run --rm veritai python -m veritai.base estatisticas
docker compose restart veritai               # a API passa a usar a base (e exige vetores do perfil em uso)
```

**Modelos dentro da imagem (opcional):** `docker build --build-arg PERFIL_EMBUTIDO=v1 -t veritai:v1 .` baixa os modelos do perfil para `/cache` durante o build. Isso serve para rodar sem volume de cache, por exemplo no Kubernetes. Atenção:
- um volume nomeado vazio montado em `/cache` recebe uma cópia desses modelos;
- um *bind mount* (pasta do host) esconde os modelos embutidos.

**Arquitetura:** o build local gera a imagem para a arquitetura da máquina; aqui foi validado em `linux/arm64`. Para máquinas x86:

```bash
docker buildx build --platform linux/amd64 -t veritai:amd64 --load .
```

Em um Mac ARM, esse build é lento porque roda por emulação. A imagem oficial para a nuvem será construída depois no GitHub Actions. A AWS também oferece instâncias ARM (Graviton), que usam a imagem arm64.

## Avaliação

```bash
.venv/bin/python eval/avaliar.py --perfil v1 --arquivo eval/contraste.jsonl
```

Formato do conjunto, métricas e regras de uso estão em [`eval/README.md`](eval/README.md). O `eval/contraste.jsonl` é só teste de sanidade: não mede desempenho e não pode ser usado em treino nem em calibração.

## Contrato

`POST /analisar` recebe a notícia, as afirmações (1 a 10), as evidências com texto enviadas pelo publisher e o número de anexos sem texto. Devolve, para cada afirmação:

- `resultado`: `SUPPORTED`, `REFUTED`, `NOT_ENOUGH_EVIDENCE` ou `CONFLICTING_EVIDENCE`;
- `texto_publico`: frase pronta para o leitor (“Resultado da verificação VeritAI: …”);
- `avaliavel`, `porcentagem` e `motivo_nao_avaliavel`;
- `fontes_independentes`, `evidencias` (fonte, URL, data, trecho, relação, origem), `checagens_anteriores`;
- `justificativa` e `limitacoes`.

O relatório também traz `avisos` (falhas de busca) e `versoes` (modelo, NLI, embedding, configuração e base de evidências). Os modelos de dados ficam em `veritai/relatorio.py`.

**Data da notícia:** `noticia.data` deve vir em `AAAA-MM-DD`. Com ela, as evidências de qualquer origem publicadas em dias posteriores ficam de fora; as do mesmo dia entram. As evidências sem data entram e o relatório registra isso em `limitacoes`. Uma data em outro formato não filtra nada e também gera uma limitação.

**Fontes independentes:** páginas do mesmo domínio e cópias quase literais, de qualquer origem, contam como uma única fonte. Matérias reescritas da mesma origem ainda contam como independentes.

## Base própria

A VeritAI busca evidências e checagens anteriores numa base SQLite rastreável (`VERITAI_BASE`, padrão `data/veritai.sqlite3`). A busca combina BM25 e vetores, e o relatório registra o snapshot da base em `versoes["base_evidencias"]`. Sem o arquivo do banco, o serviço funciona só com web e publisher.

```bash
.venv/bin/python -m veritai.base importar-textos documento.txt --data 2026-03-01
.venv/bin/python -m veritai.base indexar --perfil v0
.venv/bin/python -m veritai.base estatisticas
```

Comandos, esquema do banco e limitações estão em [`veritai/base/README.md`](veritai/base/README.md). A base é carregada só a partir da lista de fontes confiáveis definida pelo grupo, sem coleta em massa. As notícias fictícias do FOMO nunca entram.

## Estrutura

```text
veritai/
├── api.py         API HTTP
├── pipeline.py    orquestra a análise e monta o relatório
├── fontes.py      descoberta e leitura de fontes públicas
├── modelos.py     comparação por similaridade e NLI
├── regras.py      agregação dos trechos em um resultado
├── copias.py      agrupamento de fontes (domínio e cópias quase literais)
├── base/          base própria em SQLite: carga, busca BM25 + vetorial, linha de comando
├── relatorio.py   contrato de entrada e saída
├── config.py      configuração e segredos do ambiente
└── config.json    perfis de modelo, seleção e limiares versionados
eval/              script de avaliação e conjunto de contraste
tests/             testes do pipeline, da API, da base e da avaliação
```

## Princípios

- Resultado por afirmação, sem média por notícia.
- Falta de evidência nunca vira contradição.
- Nenhuma porcentagem sem calibração em dados rotulados; limiares definidos por avaliação.
- Páginas do mesmo domínio contam como uma única fonte.
- A busca na web descobre documentos, mas não decide o resultado.
- Imagens e documentos sem texto não são avaliados pela IA.
- Nenhuma LLM decide o resultado.

A documentação completa da arquitetura fica no repositório [FOMO-veritAI/fomo-docs](https://github.com/FOMO-veritAI/fomo-docs).
