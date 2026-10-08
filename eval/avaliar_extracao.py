"""Avalia a extração de afirmações por regras (veritai/extracao.py) no CheckThat! 2025, Tarefa 2, em português.

Uso:
    .venv/bin/python eval/avaliar_extracao.py caminho/checkthat2025_t2 [--split test|dev]

A pasta precisa de train-por.csv, dev-por.csv e task2_por_gold.csv (colunas "post" e "normalized claim").
Os posts são de rede social e a alegação de referência é uma reescrita que traz informação de
fora do post (imagem, checagem); por isso, a sobreposição tem teto baixo (EDA da FOP, seção 6).
A métrica oficial (METEOR) não é calculada aqui, para não acrescentar dependência; as medidas
são F1 de palavras, ROUGE-L e cobertura das palavras de conteúdo da referência.

Limpeza do post como na EDA: tira o "None" final e as repetições do mesmo texto.
Alegações idênticas a alguma do treino são relatadas à parte (58% do teste), porque um extrator
ajustado com o treino poderia memorizá-las; esta abordagem não usa o treino.
"""

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veritai.extracao import extrair, frases  # noqa: E402
from veritai.verificador import sem_acentos  # noqa: E402

PARADAS = set(
    "a o as os um uma uns umas de da do das dos em no na nos nas por pelo pela pelos pelas para com sem sob sobre e ou que se "
    "ao aos à às é foi são ser está estão não mais muito como seu sua seus suas ele ela eles elas isso este esta esse essa".split()
)


def limpar(post: str) -> str:
    texto = re.sub(r"\s+", " ", str(post)).strip()
    texto = re.sub(r"\s*None\s*$", "", texto)
    for k in range(20, len(texto) // 2 + 1):
        segmento = texto[:k].strip()
        if len(segmento) >= 20 and texto[k:].lstrip().startswith(segmento):
            resto = texto[k:].lstrip()
            while resto.startswith(segmento):
                resto = resto[len(segmento) :].lstrip()
            return (segmento + (" " + resto if resto else "")).strip()
    return texto


def palavras(texto: str) -> list[str]:
    return re.findall(r"\w+", sem_acentos(texto))


def f1(candidata: str, referencia: str) -> float:
    c, r = palavras(candidata), palavras(referencia)
    comuns = sum(min(c.count(p), r.count(p)) for p in set(c))
    if not comuns:
        return 0.0
    precisao, revocacao = comuns / len(c), comuns / len(r)
    return 2 * precisao * revocacao / (precisao + revocacao)


def rouge_l(candidata: str, referencia: str) -> float:
    c, r = palavras(candidata), palavras(referencia)
    if not c or not r:
        return 0.0
    anterior = [0] * (len(r) + 1)
    for pc in c:
        atual = [0]
        for j, pr in enumerate(r, start=1):
            atual.append(anterior[j - 1] + 1 if pc == pr else max(anterior[j], atual[j - 1]))
        anterior = atual
    lcs = anterior[-1]
    if not lcs:
        return 0.0
    precisao, revocacao = lcs / len(c), lcs / len(r)
    return 2 * precisao * revocacao / (precisao + revocacao)


def cobertura(candidata: str, referencia: str) -> float:
    conteudo = {p for p in palavras(referencia) if p not in PARADAS}
    return len(conteudo & set(palavras(candidata))) / len(conteudo) if conteudo else 0.0


def ler(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as arquivo:
        return list(csv.DictReader(arquivo))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pasta", type=Path)
    parser.add_argument("--split", choices=["test", "dev"], default="test")
    args = parser.parse_args()
    treino = {re.sub(r"\s+", " ", l["normalized claim"].lower().strip()) for l in ler(args.pasta / "train-por.csv")}
    linhas = ler(args.pasta / ("task2_por_gold.csv" if args.split == "test" else "dev-por.csv"))

    sistemas = {"extrator (1ª candidata)": [], "post inteiro (limpo)": [], "primeira frase do post": []}
    grupos, vazias, por_post = [], 0, []
    for linha in linhas:
        post, referencia = limpar(linha["post"]), linha["normalized claim"]
        candidatas = extrair("", post)
        vazias += not candidatas
        por_post.append(len(candidatas))
        saidas = {
            "extrator (1ª candidata)": candidatas[0].texto if candidatas else "",
            "post inteiro (limpo)": post,
            "primeira frase do post": (frases(post) or [""])[0],
        }
        for nome, saida in saidas.items():
            sistemas[nome].append((f1(saida, referencia), rouge_l(saida, referencia), cobertura(saida, referencia)))
        grupos.append("repetida do treino" if re.sub(r"\s+", " ", referencia.lower().strip()) in treino else "inédita")

    print(f"CheckThat! 2025 T2 PT, {args.split}: {len(linhas)} posts")
    print(f"Posts sem nenhuma candidata: {vazias} ({vazias / len(linhas):.1%}); candidatas por post: mediana {statistics.median(por_post):.0f}, máx {max(por_post)}")
    for recorte in ("todos", "inédita", "repetida do treino"):
        indices = [i for i, g in enumerate(grupos) if recorte == "todos" or g == recorte]
        print(f"\n{recorte} (n={len(indices)})  F1 de palavras | ROUGE-L | cobertura da referência")
        for nome, valores in sistemas.items():
            medias = [statistics.mean(valores[i][k] for i in indices) for k in range(3)]
            print(f"  {nome:<26} {medias[0]:.3f} | {medias[1]:.3f} | {medias[2]:.3f}")


if __name__ == "__main__":
    main()
