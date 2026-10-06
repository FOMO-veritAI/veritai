"""Agrupamento de fontes que não são independentes: mesmo domínio ou cópia quase literal.

Usa shingles de palavras e similaridade de Jaccard. Só pega cópias quase
literais; matérias reescritas da mesma origem continuam contando como
independentes.
"""

import re

from .config import COPIAS
from .fontes import Fonte


def shingles(texto: str, tamanho: int | None = None) -> set[int]:
    tamanho = tamanho or COPIAS["palavras_por_shingle"]
    palavras = re.findall(r"\w+", texto.lower())
    if len(palavras) < tamanho:
        return {hash(tuple(palavras))} if palavras else set()
    return {hash(tuple(palavras[i : i + tamanho])) for i in range(len(palavras) - tamanho + 1)}


def jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def sao_copias(a: set[int], b: set[int]) -> bool:
    return jaccard(a, b) >= COPIAS["jaccard_minimo"]


def agrupar(fontes: list[Fonte]) -> None:
    """Preenche fonte.grupo: mesmo domínio, mesmo grupo da base ou cópia quase literal ficam juntos."""
    pais = list(range(len(fontes)))

    def raiz(i: int) -> int:
        while pais[i] != i:
            pais[i] = pais[pais[i]]
            i = pais[i]
        return i

    def unir(i: int, j: int) -> None:
        pais[raiz(i)] = raiz(j)

    primeira_por_chave: dict[str, int] = {}
    for i, fonte in enumerate(fontes):
        for chave in (f"dominio:{fonte.dominio}", f"copia:{fonte.copia_de}" if fonte.copia_de else ""):
            if chave:
                if chave in primeira_por_chave:
                    unir(i, primeira_por_chave[chave])
                else:
                    primeira_por_chave[chave] = i
    conjuntos = [shingles(fonte.texto_completo or fonte.texto) for fonte in fontes]
    for i in range(len(fontes)):
        for j in range(i + 1, len(fontes)):
            if raiz(i) != raiz(j) and sao_copias(conjuntos[i], conjuntos[j]):
                unir(i, j)
    for i, fonte in enumerate(fontes):
        fonte.grupo = f"grupo:{fontes[raiz(i)].dominio}"
