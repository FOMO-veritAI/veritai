"""Avalia um perfil de modelos sobre um conjunto rotulado em JSONL.

Uso:
    .venv/bin/python eval/avaliar.py --perfil v1 --arquivo eval/contraste.jsonl [--predicoes saida.jsonl]

Cada linha é comparada só com as evidências do próprio arquivo (sem busca na
web), pelas mesmas regras do serviço. O formato está em eval/README.md.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veritai.copias import agrupar  # noqa: E402
from veritai.fontes import Fonte, dominio_da_fonte  # noqa: E402

CLASSES = ["SUPPORTED", "REFUTED", "NOT_ENOUGH_EVIDENCE", "CONFLICTING_EVIDENCE"]


def carregar(caminho: str | Path) -> list[dict]:
    itens = []
    for numero, linha in enumerate(Path(caminho).read_text(encoding="utf-8").splitlines(), start=1):
        if not linha.strip():
            continue
        item = json.loads(linha)
        faltando = {"afirmacao", "evidencias", "rotulo", "evento", "data", "fonte", "justificativa"} - set(item)
        if faltando:
            raise ValueError(f"Linha {numero}: campos ausentes {sorted(faltando)}.")
        if item["rotulo"] not in CLASSES:
            raise ValueError(f"Linha {numero}: rótulo inválido {item['rotulo']!r}.")
        itens.append(item)
    return itens


def fontes_do_item(item: dict) -> list[Fonte]:
    # Evidências do conjunto são texto fornecido (anexo); a fonte independente segue a mesma regra do serviço.
    fontes = []
    for evidencia in item["evidencias"]:
        url = evidencia.get("url", "")
        dominio = dominio_da_fonte(url, evidencia["fonte"])
        fontes.append(Fonte(url=url, titulo=evidencia["fonte"], dominio=dominio, texto=evidencia["texto"], origem="anexo", data_publicacao=evidencia.get("data")))
    return fontes


def matriz_confusao(rotulos: list[str], preditos: list[str]) -> dict[str, dict[str, int]]:
    matriz = {real: dict.fromkeys(CLASSES, 0) for real in CLASSES}
    for real, predito in zip(rotulos, preditos):
        matriz[real][predito] += 1
    return matriz


def f1_por_classe(rotulos: list[str], preditos: list[str]) -> dict[str, float | None]:
    """F1 de cada classe; None quando a classe não aparece nem nos rótulos nem nas predições."""
    resultado: dict[str, float | None] = {}
    for classe in CLASSES:
        vp = sum(r == classe and p == classe for r, p in zip(rotulos, preditos))
        fp = sum(r != classe and p == classe for r, p in zip(rotulos, preditos))
        fn = sum(r == classe and p != classe for r, p in zip(rotulos, preditos))
        resultado[classe] = None if vp + fp + fn == 0 else 2 * vp / (2 * vp + fp + fn)
    return resultado


def macro_f1(rotulos: list[str], preditos: list[str]) -> float:
    valores = [valor for valor in f1_por_classe(rotulos, preditos).values() if valor is not None]
    return sum(valores) / len(valores) if valores else 0.0


def taxa_falsas_aprovacoes(rotulos: list[str], preditos: list[str]) -> float | None:
    """Fração dos itens com rótulo diferente de SUPPORTED que o modelo marcou como SUPPORTED.

    Entram REFUTED, NOT_ENOUGH_EVIDENCE e CONFLICTING_EVIDENCE.
    """
    negativos = [p for r, p in zip(rotulos, preditos) if r != "SUPPORTED"]
    return sum(p == "SUPPORTED" for p in negativos) / len(negativos) if negativos else None


def bootstrap(metrica, rotulos: list[str], preditos: list[str], repeticoes: int = 1000, semente: int = 0) -> tuple[float, float] | None:
    """Intervalo de 95% por percentis, reamostrando itens com reposição."""
    sorteio = random.Random(semente)
    valores = []
    for _ in range(repeticoes):
        indices = [sorteio.randrange(len(rotulos)) for _ in rotulos]
        valor = metrica([rotulos[i] for i in indices], [preditos[i] for i in indices])
        if valor is not None:
            valores.append(valor)
    if not valores:
        return None
    valores.sort()
    return valores[int(0.025 * (len(valores) - 1))], valores[int(0.975 * (len(valores) - 1))]


def metricas(rotulos: list[str], preditos: list[str], repeticoes: int = 1000, semente: int = 0) -> dict:
    return {
        "n": len(rotulos),
        "macro_f1": macro_f1(rotulos, preditos),
        "macro_f1_ic95": bootstrap(macro_f1, rotulos, preditos, repeticoes, semente),
        "f1_por_classe": f1_por_classe(rotulos, preditos),
        "taxa_falsas_aprovacoes": taxa_falsas_aprovacoes(rotulos, preditos),
        "taxa_falsas_aprovacoes_ic95": bootstrap(taxa_falsas_aprovacoes, rotulos, preditos, repeticoes, semente),
        "matriz_confusao": matriz_confusao(rotulos, preditos),
    }


def prever(itens: list[dict], comparador) -> list[dict]:
    from veritai.regras import agregar

    predicoes = []
    for item in itens:
        fontes = fontes_do_item(item)
        agrupar(fontes)
        avaliados = comparador.comparar(item["afirmacao"], fontes)
        predicoes.append({
            "id": item.get("id"),
            "par": item.get("par"),
            "afirmacao": item["afirmacao"],
            "rotulo": item["rotulo"],
            "predito": agregar(avaliados).resultado,
            "similaridade_maxima": max((avaliado.similaridade for avaliado in avaliados), default=None),
        })
    return predicoes


def formatar(perfil: str, resultado: dict, tempos: dict) -> str:
    def numero(valor):
        return "—" if valor is None else f"{valor:.3f}"

    def intervalo(valor):
        return "—" if valor is None else f"[{valor[0]:.3f}, {valor[1]:.3f}]"

    linhas = [
        f"Perfil {perfil}: {resultado['n']} itens; carga {tempos['carga_s']:.1f} s, inferência {tempos['inferencia_s']:.1f} s",
        f"macro-F1 {numero(resultado['macro_f1'])} IC95 {intervalo(resultado['macro_f1_ic95'])}",
        f"falsas aprovações {numero(resultado['taxa_falsas_aprovacoes'])} IC95 {intervalo(resultado['taxa_falsas_aprovacoes_ic95'])}",
        "F1 por classe: " + ", ".join(f"{classe} {numero(valor)}" for classe, valor in resultado["f1_por_classe"].items()),
        "Matriz de confusão (linha = rótulo, coluna = predição; S, R, NEE, CE):",
    ]
    for real, linha in resultado["matriz_confusao"].items():
        linhas.append(f"  {real:<22}" + " ".join(f"{linha[classe]:>4}" for classe in CLASSES))
    return "\n".join(linhas)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--perfil", required=True)
    parser.add_argument("--arquivo", required=True)
    parser.add_argument("--predicoes", help="grava as predições por item em JSONL")
    parser.add_argument("--repeticoes", type=int, default=1000)
    parser.add_argument("--semente", type=int, default=0)
    args = parser.parse_args()

    from veritai.modelos import ComparadorNLI

    itens = carregar(args.arquivo)
    comparador = ComparadorNLI(args.perfil)
    inicio = time.perf_counter()
    comparador.modelos._carregar()
    carga = time.perf_counter() - inicio
    inicio = time.perf_counter()
    predicoes = prever(itens, comparador)
    inferencia = time.perf_counter() - inicio
    resultado = metricas([p["rotulo"] for p in predicoes], [p["predito"] for p in predicoes], args.repeticoes, args.semente)
    print(formatar(comparador.perfil, resultado, {"carga_s": carga, "inferencia_s": inferencia}))
    similaridades = [p["similaridade_maxima"] for p in predicoes if p["similaridade_maxima"] is not None]
    if similaridades:
        print(f"Maior similaridade entre as janelas do relatório, por item: mín {min(similaridades):.3f}, máx {max(similaridades):.3f} ({len(similaridades)} de {len(predicoes)} itens chegaram ao NLI)")
    if args.predicoes:
        Path(args.predicoes).write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in predicoes), encoding="utf-8")


if __name__ == "__main__":
    main()
