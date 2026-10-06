"""Regras explícitas que agregam os trechos avaliados em um resultado por afirmação."""

from dataclasses import dataclass

from .config import LIMIARES
from .modelos import TrechoAvaliado


@dataclass
class Agregado:
    resultado: str
    dominios_a_favor: set[str]
    dominios_contra: set[str]

    @property
    def fontes_independentes(self) -> int:
        # Várias páginas do mesmo domínio contam como uma única comprovação.
        return len(self.dominios_a_favor | self.dominios_contra)


def forte(item: TrechoAvaliado) -> bool:
    return item.similaridade >= LIMIARES["similaridade_forte"] and item.pontuacao_nli >= LIMIARES["nli_forte"]


def agregar(itens: list[TrechoAvaliado]) -> Agregado:
    fortes = [item for item in itens if forte(item)]
    a_favor = {item.fonte.dominio for item in fortes if item.relacao == "entailment"}
    contra = {item.fonte.dominio for item in fortes if item.relacao == "contradiction"}
    if a_favor and contra:
        resultado = "CONFLICTING_EVIDENCE"
    elif contra:
        resultado = "REFUTED"
    elif a_favor:
        resultado = "SUPPORTED"
    else:
        resultado = "NOT_ENOUGH_EVIDENCE"
    return Agregado(resultado, a_favor, contra)
