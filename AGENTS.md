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

Implementado: API `POST /analisar` e `GET /health`; pipeline V0 (similaridade `paraphrase-multilingual-MiniLM-L12-v2` + NLI `multilingual-MiniLMv2-L6-mnli-xnli` + regras); busca em Bing News RSS, GDELT e Google Fact Check opcional; evidências enviadas pelo publisher; contagem de fontes independentes por domínio; 12 testes.

Planejado, nesta ordem:
1. Baseline V1: NLI `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` e embedding `intfloat/multilingual-e5-base` ou `BAAI/bge-m3`.
2. Base própria de evidências e checagens (SQLite agora; PostgreSQL + pgvector depois), busca BM25 + vetorial com fusão RRF, importação de ClaimReview.
3. Script de avaliação (`eval/`) e primeira medição V0 × V1.
4. Modelo treinado V2: `xlm-roberta-base` com ajuste fino em 4 classes sobre (afirmação, trecho). Só substitui a V1 se tiver macro-F1 maior e taxa de falsas aprovações igual ou menor na validação.
5. Calibração (temperature scaling ou Platt) e limiares de suficiência; só então liberar a porcentagem.

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

Separação treino/validação/calibração/teste por evento, período e fonte; teste usado uma única vez. Métricas principais: macro-F1 e taxa de falsas aprovações. Também: precisão de apoia/contradiz, Recall@5 e MRR, Brier score, ECE e curva de calibração, cobertura × erro. Intervalos de confiança por bootstrap.

## Como rodar e testar

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[modelos,dev]"
.venv/bin/python -m pytest
.venv/bin/python -m uvicorn veritai.api:app --host 127.0.0.1 --port 8100
```

Os testes usam modelos e busca simulados e não precisam do extra `modelos`. Rode os testes antes de considerar qualquer tarefa concluída e informe o resultado real.

## Segurança e git

- Nunca leia, imprima ou envie o conteúdo de `.env`. Segredos só em variáveis de ambiente.
- Não versione `data/`, `models/`, bancos, pesos de modelos nem ambientes virtuais.
- Commits pequenos, com testes passando. Não faça push sem autorização do desenvolvedor.
- Escreva código no estilo do que já existe: nomes em português, comentários curtos só onde o motivo não é óbvio.
