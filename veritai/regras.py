"""Regras explícitas que agregam os trechos avaliados em um resultado por afirmação."""

from dataclasses import dataclass

from .config import LIMIARES
from .modelos import TrechoAvaliado


@dataclass
class Agregado:
    resultado: str
    grupos_a_favor: set[str]
    grupos_contra: set[str]

    @property
    def fontes_independentes(self) -> int:
        # Páginas do mesmo domínio e cópias quase literais contam como uma única comprovação.
        return len(self.grupos_a_favor | self.grupos_contra)


def forte(item: TrechoAvaliado) -> bool:
    return item.similaridade >= LIMIARES["similaridade_forte"] and item.pontuacao_nli >= LIMIARES["nli_forte"]


def grupo(item: TrechoAvaliado) -> str:
    # Sem agrupamento calculado (fonte.grupo vazio), vale o domínio.
    return item.fonte.grupo or item.fonte.dominio


def agregar(itens: list[TrechoAvaliado]) -> Agregado:
    fortes = [item for item in itens if forte(item)]
    a_favor = {grupo(item) for item in fortes if item.relacao == "entailment"}
    contra = {grupo(item) for item in fortes if item.relacao == "contradiction"}
    if a_favor and contra:
        resultado = "CONFLICTING_EVIDENCE"
    elif contra:
        resultado = "REFUTED"
    elif a_favor:
        resultado = "SUPPORTED"
    else:
        resultado = "NOT_ENOUGH_EVIDENCE"
    return Agregado(resultado, a_favor, contra)


CONTA_COMO = {"entailment": "apoio", "contradiction": "contradicao"}

REGRAS = {
    "CONFLICTING_EVIDENCE": "Há fonte independente com apoio forte e fonte independente com contradição forte.",
    "REFUTED": "Há contradição forte de ao menos uma fonte independente e nenhum apoio forte.",
    "SUPPORTED": "Há apoio forte de ao menos uma fonte independente e nenhuma contradição forte.",
    "NOT_ENOUGH_EVIDENCE": "Nenhum trecho passou dos dois limiares (similaridade e NLI) com apoio ou contradição.",
}


def explicar(itens: list[TrechoAvaliado]) -> dict:
    """Explica a agregação, item a item, com as mesmas regras de agregar (uso interno, ex.: demonstração)."""
    agregado = agregar(itens)
    return {
        "limiares": {"similaridade_forte": LIMIARES["similaridade_forte"], "nli_forte": LIMIARES["nli_forte"]},
        "itens": [
            {
                "fonte": item.fonte.titulo,
                "grupo": grupo(item),
                "trecho": item.trecho,
                "relacao": item.relacao,
                "similaridade": item.similaridade,
                "pontuacao_nli": item.pontuacao_nli,
                "passa_similaridade_forte": item.similaridade >= LIMIARES["similaridade_forte"],
                "passa_nli_forte": item.pontuacao_nli >= LIMIARES["nli_forte"],
                "forte": forte(item),
                # Só apoio ou contradição fortes contam; um trecho neutro forte não decide nada.
                "conta_como": CONTA_COMO.get(item.relacao) if forte(item) else None,
            }
            for item in itens
        ],
        "grupos_a_favor": sorted(agregado.grupos_a_favor),
        "grupos_contra": sorted(agregado.grupos_contra),
        "fontes_independentes": agregado.fontes_independentes,
        "resultado": agregado.resultado,
        "regra": REGRAS[agregado.resultado],
    }
