# VeritAI

Serviço de IA do **FOMO** (*Fear of Missing Objectivity*) que analisa afirmações factuais de uma notícia antes da publicação e devolve um relatório por afirmação, com evidências, fontes e limitações. O nome une *veritas* (“a verdade”, em latim) e *AI*.

A VeritAI recomenda e documenta. A decisão de publicar é sempre de um revisor humano, na plataforma FOMO ([FOMO-veritAI/fomo](https://github.com/FOMO-veritAI/fomo)).

## Estado atual

| Componente | Situação |
|---|---|
| API `POST /analisar` com relatório por afirmação | Implementado |
| Pipeline V0: similaridade (MiniLM) + NLI (MiniLMv2) + regras | Implementado, migrado do protótipo |
| Busca de fontes: Bing News RSS, GDELT, Google Fact Check (opcional) | Implementado |
| Evidências enviadas pelo publisher (links e documentos com texto) | Implementado |
| Contagem de fontes independentes por domínio | Implementado |
| Baseline V1 (mDeBERTa-v3 + e5/bge-m3) | Planejado |
| Base própria de evidências e checagens (busca textual + vetorial) | Planejado |
| Modelo treinado V2 (XLM-RoBERTa ajustado) | Planejado, depende de dados rotulados |
| Porcentagem calibrada | Planejado, depende de treino, teste e calibração |

**Hoje não existe porcentagem.** O campo `porcentagem` vem sempre `null` e `avaliavel` vem `false`, com o motivo registrado em `motivo_nao_avaliavel`. Os limiares em `veritai/config.json` são herdados do protótipo e ainda não foram validados com dados.

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
```

Os testes usam modelos e busca simulados: rodam em menos de um segundo e não precisam do extra `modelos`.

## Contrato

`POST /analisar` recebe a notícia, as afirmações (1 a 10), as evidências com texto enviadas pelo publisher e o número de anexos sem texto. Devolve, para cada afirmação:

- `resultado`: `SUPPORTED`, `REFUTED`, `NOT_ENOUGH_EVIDENCE` ou `CONFLICTING_EVIDENCE`;
- `texto_publico`: frase pronta para o leitor (“Resultado da verificação VeritAI: …”);
- `avaliavel`, `porcentagem` e `motivo_nao_avaliavel`;
- `fontes_independentes`, `evidencias` (fonte, URL, data, trecho, relação, origem), `checagens_anteriores`;
- `justificativa` e `limitacoes`.

O relatório também traz `avisos` (falhas de busca) e `versoes` (modelo, NLI, embedding, configuração e base de evidências). Os modelos de dados ficam em `veritai/relatorio.py`.

## Estrutura

```text
veritai/
├── api.py         API HTTP
├── pipeline.py    orquestra a análise e monta o relatório
├── fontes.py      descoberta e leitura de fontes públicas
├── modelos.py     comparação por similaridade e NLI
├── regras.py      agregação dos trechos em um resultado
├── relatorio.py   contrato de entrada e saída
├── config.py      configuração e segredos do ambiente
└── config.json    modelos e limiares versionados
tests/             testes do pipeline e da API
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
