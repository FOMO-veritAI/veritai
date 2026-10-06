# Avaliação da VeritAI

`avaliar.py` roda um perfil de modelos (`v0`, `v1`) sobre um conjunto rotulado em JSONL e calcula as métricas. Cada item é comparado só com as evidências do próprio arquivo, sem busca na web, pelas mesmas regras do serviço (`veritai/regras.py`).

```bash
.venv/bin/python eval/avaliar.py --perfil v1 --arquivo eval/contraste.jsonl --predicoes /tmp/pred_v1.jsonl
```

Precisa do extra `modelos` (`pip install -e ".[modelos]"`). Na primeira execução, os modelos do perfil são baixados para o cache do Hugging Face, fora do repositório.

## Formato do conjunto (JSONL, um item por linha)

| Campo | Obrigatório | Conteúdo |
|---|---|---|
| `afirmacao` | sim | Afirmação a verificar. |
| `evidencias` | sim | Lista de `{"texto", "fonte", "data"}`; `url` é opcional. A fonte independente é o domínio da `url`, ou o nome em `fonte` quando não há URL. |
| `rotulo` | sim | `SUPPORTED`, `REFUTED`, `NOT_ENOUGH_EVIDENCE` ou `CONFLICTING_EVIDENCE`. |
| `evento` | sim | Identificador do evento. A separação treino/validação/calibração/teste deve ser feita por evento, período e fonte; o script ainda não faz nem verifica essa separação. |
| `data` | sim | Data do evento ou da afirmação (`AAAA-MM-DD`). |
| `fonte` | sim | Origem do item rotulado (veículo, agência ou conjunto). |
| `justificativa` | sim | Por que o rótulo foi atribuído. |
| `id`, `par` | não | Identificador do item e do grupo de contraste, usados só nos relatórios. |

As evidências entram como texto fornecido (origem `anexo`): nenhuma frase é descartada pelo tamanho. A fonte independente segue a regra do serviço: o domínio da `url` quando houver, senão o nome em `fonte`.

## Métricas

- **Macro-F1** sobre as classes que aparecem nos rótulos ou nas predições. Uma classe ausente de ambos fica como `—` e não entra na média.
- **F1 por classe.**
- **Taxa de falsas aprovações:** fração dos itens com rótulo diferente de `SUPPORTED` (`REFUTED`, `NOT_ENOUGH_EVIDENCE` ou `CONFLICTING_EVIDENCE`) que o modelo marcou como `SUPPORTED`.
- **Matriz de confusão** (linha = rótulo, coluna = predição).
- **Intervalo de confiança de 95%** por bootstrap: 1000 reamostragens dos itens com reposição, semente fixa, percentis 2,5 e 97,5.

O script também informa o tempo de carga dos modelos e o de inferência.

## Limiares

Os limiares em `veritai/config.json` (`similaridade_selecao`, `similaridade_forte`, `nli_forte`) vêm do protótipo V0 e **não foram validados para nenhum perfil**. Eles terão de ser definidos **por perfil**, na avaliação com dados rotulados (conjunto de validação, nunca o de teste).

Na V1, o filtro de similaridade atual **praticamente não atua**. O e5 dá similaridades altas a quase qualquer par de frases em português. Observação exploratória de 2026-10-06, no conjunto de contraste:
- a similaridade entre afirmação e evidência ficou entre 0,80 e 0,87 em todos os itens;
- um par sem relação nenhuma deu 0,79;
- com os limiares de 0,38 e 0,43, nenhuma janela foi descartada.

A faixa por item aparece na saída do `avaliar.py` ("Maior similaridade entre as janelas do relatório"). Ela considera só as janelas que ficam no relatório depois da consolidação.

O perfil `v0` mantém os modelos da V0, mas usa a seleção nova: janelas de 1 a 2 frases, 3 janelas por fonte e a melhor janela de apoio e de contradição de cada fonte. Os resultados dele não são comparáveis com saídas antigas da V0, de antes de `versao_config` 2026-10-06.2.

## `contraste.jsonl`: só teste de sanidade

São 28 itens escritos à mão sobre fatos inventados e neutros, nenhum tirado da interface do FOMO:

- 12 pares em que a mesma evidência apoia uma afirmação e contradiz uma variação mínima dela, cobrindo dobrou/não dobrou, 8.200/8,2 mil/82 mil, 2 p.p./2%, troca de pessoa e de papéis, negação, data e maioria/minoria;
- 4 itens cuja resposta não está na evidência (`NOT_ENOUGH_EVIDENCE`).

**Ele não mede desempenho.** É pequeno, sintético e escrito por quem conhece os modelos, e serve só para ver onde cada perfil erra em casos de contraste conhecidos.

**Não pode ser usado como dado de treino nem de calibração**, nem para escolher limiares. As métricas sobre ele não podem ser apresentadas como desempenho da VeritAI.
