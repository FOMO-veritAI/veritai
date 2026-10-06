# Instruções para agentes: VeritAI

Este arquivo vale para qualquer agente que trabalhe neste repositório (Claude Code, Codex e outros). Responda e escreva em português do Brasil.

## O projeto

A **VeritAI** é o serviço de IA do **FOMO** (*Fear of Missing Objectivity*), uma plataforma de notícias em que nenhuma matéria chega ao leitor sem verificação. A VeritAI analisa cada afirmação factual de uma notícia e devolve um relatório com evidências, fontes, justificativa e limitações. A decisão de publicar é sempre de um revisor humano, na plataforma ([FOMO-veritAI/fomo](https://github.com/FOMO-veritAI/fomo)), que consome este serviço pela API.

Contexto acadêmico: Challenge 1 (Fake News / Desinformação) de uma residência em IA. Pergunta central: como a IA ajuda a avaliar a confiabilidade de informações sem substituir o pensamento crítico.

## Arquitetura-alvo

- **A. Preparação (periódica):** base de checagens anteriores → base de evidências com origem, data e trechos indexados (busca textual + vetorial) → dados rotulados em português → treino e teste do modelo → calibração, limiares e versionamento.
- **B. Análise (por notícia):** afirmações e contexto → checagens equivalentes (confirmar mesmo contexto e período; nunca encerram a análise) → recuperação de evidências → modelo + regras explícitas → calibração → evidência suficiente? sim: porcentagem; não: "não avaliável" → relatório.
- **C. Decisão:** revisão humana obrigatória → publicar ou devolver (feito no FOMO).

## Estado atual (diferencie sempre implementado de planejado)

Implementado: API `POST /analisar` e `GET /health`; perfis de modelo em `config.json`, escolhidos por `VERITAI_PERFIL` (padrão `v0`); perfil V0 (similaridade `paraphrase-multilingual-MiniLM-L12-v2` + NLI `multilingual-MiniLMv2-L6-mnli-xnli` + regras; mantém os modelos da V0, mas usa a seleção nova de janelas); baseline V1 (embedding `intfloat/multilingual-e5-base` com prefixos `query:`/`passage:` + NLI `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`), ainda não medida em dados rotulados; janelas de 1 a 2 frases, com as 3 mais similares de cada fonte levadas ao NLI, em até 6 fontes, priorizando domínios diferentes; busca em Bing News RSS e GDELT; Google Fact Check opcional, só para listar checagens anteriores; evidências enviadas pelo publisher; base própria em SQLite (`veritai/base/`, caminho em `VERITAI_BASE`): documentos com origem, data, hash e trechos de cerca de 150 palavras, busca BM25 (FTS5) + vetorial exata (numpy) com fusão RRF, vetores por embedding do perfil (perfil sem índice dá erro), checagens ClaimReview com veredito original da agência e snapshot por carga em `versoes["base_evidencias"]`; fontes independentes contadas por grupo (mesmo domínio ou cópia quase literal por shingles + Jaccard, em todas as origens); filtro por `noticia.data` (AAAA-MM-DD, por dia) em todas as origens; leitura de URLs com DNS resolvido uma única vez (todos os endereços públicos, conexão no IP validado, SNI e certificado pelo nome original, sem redirecionamentos nem proxy do ambiente); container (Dockerfile multi-stage com Python 3.12 e torch só-CPU, usuário 10001, modelos em `/cache` e base em `/data` por volume, estágio `teste`, docker-compose), validado localmente em arm64; `eval/avaliar.py` (macro-F1, F1 por classe, falsas aprovações, matriz de confusão, IC por bootstrap) e `eval/contraste.jsonl` (28 itens, só sanidade); 105 testes (104 no container, onde o teste do `.gitignore` é pulado por falta de git).

Os limiares de `config.json` não foram validados para nenhum perfil, nem os parâmetros de cópias e da base (Jaccard 0,8, shingles de 5 palavras, RRF k=60). O agrupamento só pega cópias quase literais; matérias reescritas da mesma origem ainda contam como independentes. Na V1, o filtro de similaridade praticamente não atua. Em observação exploratória no contraste, o e5 deu entre 0,80 e 0,87 a todos os pares; o detalhe está em `eval/README.md`.

Planejado, nesta ordem:
1. ~~Baseline V1~~ implementada como perfil `v1`; falta medir em dados rotulados reais antes de virar padrão. `BAAI/bge-m3` ainda não testado.
2. ~~Base própria em SQLite~~ implementada; falta carregá-la com a lista de fontes confiáveis definida pelo grupo e as checagens. Depois: PostgreSQL + pgvector (ou FAISS), remoção e atualização de documentos, detecção de cópias em escala e mapeamento dos vereditos das agências para as 4 classes (área de Dados).
3. Conjunto rotulado real (separado por evento, período e fonte) e primeira medição V0 × V1. O script `eval/avaliar.py` já existe; `eval/contraste.jsonl` é só sanidade e não conta como medição.
4. Modelo treinado V2: `xlm-roberta-base` com ajuste fino em 4 classes sobre (afirmação, trecho). Só substitui a V1 se tiver macro-F1 maior e taxa de falsas aprovações igual ou menor na validação.
5. Calibração (temperature scaling ou Platt) e limiares de suficiência; só então liberar a porcentagem.

Infraestrutura (em paralelo): imagem oficial construída no GitHub Actions (amd64 e arm64), manifests de Kubernetes e deploy na AWS.

## Regras que nunca podem ser quebradas

- **Nenhuma porcentagem sem calibração real.** Enquanto `config.json` tiver `"calibrado": false`, `porcentagem` é `null` e `avaliavel` é `false`. Nunca invente números nem limiares (30%, 50% etc.).
- Resultado **por afirmação**; nunca média por notícia.
- Falta de evidência é `NOT_ENOUGH_EVIDENCE`, nunca contradição.
- Páginas do mesmo domínio ou cópias da mesma fonte contam como uma única comprovação.
- A busca na web descobre documentos; ela não decide o resultado.
- Imagens e documentos sem texto não são avaliados pela IA: exigem conferência humana.
- Nenhuma LLM decide o resultado.
- Textos públicos no formato "Resultado da verificação VeritAI: …". Não use "Suportada" ou "Refutada" isolados.
- Não declare o modelo treinado ou a porcentagem como concluídos sem dados, treino, testes e calibração reais.
- Não use notícias fictícias da interface do FOMO como evidência ou dado de treino.
- Não assuma que existe um dataset "AVeriTeC-BR" validado; confirme qualquer dataset antes de usar.

## Contrato com o FOMO

Os modelos de dados estão em `veritai/relatorio.py`. Mudanças no contrato quebram a plataforma: só altere com pedido explícito e avise no resumo do trabalho.

## Avaliação

Separação treino/validação/calibração/teste por evento, período e fonte; teste usado uma única vez. Métricas principais: macro-F1 e taxa de falsas aprovações (prediz `SUPPORTED` quando o rótulo é `REFUTED`, `NOT_ENOUGH_EVIDENCE` ou `CONFLICTING_EVIDENCE`). Também: precisão de apoia/contradiz, Recall@5 e MRR, Brier score, ECE e curva de calibração, cobertura × erro. Intervalos de confiança por bootstrap.

## Como rodar e testar

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[modelos,dev]"
.venv/bin/python -m pytest
.venv/bin/python -m uvicorn veritai.api:app --host 127.0.0.1 --port 8100
docker build --target teste .     # suíte com Python 3.12, a versão do container
docker compose up -d --build      # container local (detalhes no README)
```

Os testes usam modelos e busca simulados e não precisam do extra `modelos`. Rode os testes antes de considerar qualquer tarefa concluída e informe o resultado real.

## Segurança e git

- Nunca leia, imprima ou envie o conteúdo de `.env`. Segredos só em variáveis de ambiente.
- Não versione `data/`, `models/`, bancos, pesos de modelos nem ambientes virtuais.
- Commits pequenos, com testes passando. Não faça push sem autorização do desenvolvedor.
- Escreva código no estilo do que já existe: nomes em português, comentários curtos só onde o motivo não é óbvio.
