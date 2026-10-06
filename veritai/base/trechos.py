"""Divisão de documentos em trechos de cerca de N palavras, com sobreposição, sem partir frases."""

import re

from ..config import BASE


def frases(texto: str) -> list[str]:
    return [frase.strip() for frase in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", texto)) if frase.strip()]


def dividir(texto: str, palavras: int | None = None, sobreposicao: int | None = None) -> list[str]:
    palavras = palavras or BASE["palavras_por_trecho"]
    sobreposicao = BASE["palavras_de_sobreposicao"] if sobreposicao is None else sobreposicao
    lista = frases(texto)
    tamanhos = [len(frase.split()) for frase in lista]
    trechos: list[str] = []
    inicio = 0
    while inicio < len(lista):
        fim, total = inicio, 0
        # Junta frases inteiras até chegar ao tamanho; uma frase maior que o limite vira um trecho sozinha.
        while fim < len(lista) and (total == 0 or total + tamanhos[fim] <= palavras):
            total += tamanhos[fim]
            fim += 1
        trechos.append(" ".join(lista[inicio:fim]))
        if fim >= len(lista):
            break
        # O próximo trecho recomeça nas últimas frases deste: repete frases até chegar à sobreposição,
        # sem passar do dobro dela, e sempre avança pelo menos uma frase.
        proximo, repetidas = fim, 0
        while proximo - 1 > inicio and repetidas < sobreposicao and repetidas + tamanhos[proximo - 1] <= 2 * sobreposicao:
            proximo -= 1
            repetidas += tamanhos[proximo]
        inicio = proximo
    return trechos
