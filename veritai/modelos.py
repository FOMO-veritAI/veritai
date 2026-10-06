"""Comparação afirmação × trechos por similaridade semântica e NLI.

O perfil de modelos (v0, v1) vem do config. Os modelos só são carregados na
primeira análise, para que os testes e a API subam sem baixar pesos.
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Protocol

from .config import LIMIARES, SELECAO, nome_perfil, perfil
from .fontes import Fonte


ROTULOS_NLI = {"entailment", "neutral", "contradiction"}


@dataclass
class TrechoAvaliado:
    fonte: Fonte
    trecho: str
    similaridade: float
    relacao: str  # entailment | contradiction | neutral: rótulo mais provável na janela
    pontuacao_nli: float  # probabilidade do rótulo em relacao
    probabilidades: dict[str, float] = field(default_factory=dict)


class Comparador(Protocol):
    perfil: str

    def comparar(self, afirmacao: str, fontes: list[Fonte]) -> list[TrechoAvaliado]: ...


class Modelos(Protocol):
    def similaridades(self, consulta: str, textos: list[str]) -> list[float]: ...

    def classificar(self, pares: list[tuple[str, str]]) -> list[dict[str, float]]: ...


ORIGENS_FORNECIDAS = {"link_publisher", "anexo"}


def dividir_em_trechos(texto: str, fornecido: bool = False) -> list[str]:
    frases = [frase.strip() for frase in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", texto)) if frase.strip()]
    if not fornecido:
        # Em páginas da web, frases muito curtas ou longas costumam ser menus, legendas ou blocos sem pontuação.
        return [frase[:650] for frase in frases if 45 <= len(frase) <= 800][:120]
    # Texto enviado pelo publisher nunca é descartado: frases curtas são agrupadas e longas, divididas,
    # o que mantém o número de partes limitado pelo tamanho do texto.
    partes: list[str] = []
    atual = ""
    for frase in frases:
        for inicio in range(0, len(frase), 650):
            pedaco = frase[inicio : inicio + 650]
            if atual and len(atual) + 1 + len(pedaco) <= 650:
                atual = f"{atual} {pedaco}"
            else:
                if atual:
                    partes.append(atual)
                atual = pedaco
    if atual:
        partes.append(atual)
    return partes


def janelas(texto: str, fornecido: bool = False) -> list[str]:
    """Janelas de uma frase e de duas frases consecutivas."""
    frases = dividir_em_trechos(texto, fornecido)
    return frases + [f"{a} {b}" for a, b in zip(frases, frases[1:])]


def candidatos(fontes: list[Fonte]) -> list[tuple[Fonte, str]]:
    return [(fonte, janela) for fonte in fontes for janela in janelas(fonte.texto, fonte.origem in ORIGENS_FORNECIDAS)]


def selecionar(
    pares: list[tuple[Fonte, str]], similaridades: list[float], k: int, max_fontes: int, minimo: float
) -> list[tuple[Fonte, list[tuple[str, float]]]]:
    """As k janelas mais similares de cada fonte, para até max_fontes fontes.

    Primeiro entra a fonte mais similar de cada domínio; só depois outras páginas de domínios já
    escolhidos, para que páginas do mesmo site não tirem a vez de fontes independentes.
    """
    por_fonte: dict[int, tuple[Fonte, list[tuple[str, float]]]] = {}
    for (fonte, janela), similaridade in zip(pares, similaridades):
        if similaridade >= minimo:
            por_fonte.setdefault(id(fonte), (fonte, []))[1].append((janela, float(similaridade)))
    selecionadas = []
    for fonte, itens in por_fonte.values():
        itens.sort(key=lambda item: item[1], reverse=True)
        selecionadas.append((fonte, itens[:k]))
    selecionadas.sort(key=lambda item: item[1][0][1], reverse=True)
    dominios: set[str] = set()
    primeiras, repetidas = [], []
    for fonte, itens in selecionadas:
        (repetidas if fonte.dominio in dominios else primeiras).append((fonte, itens))
        dominios.add(fonte.dominio)
    return (primeiras + repetidas)[:max_fontes]


def consolidar(fonte: Fonte, avaliadas: list[tuple[str, float, dict[str, float]]]) -> list[TrechoAvaliado]:
    """Por fonte, a janela com maior probabilidade de apoio e a com maior de contradição."""
    escolhidas = []
    for rotulo in ("entailment", "contradiction"):
        melhor = max(avaliadas, key=lambda item: item[2][rotulo])
        if melhor not in escolhidas:
            escolhidas.append(melhor)
    resultado = []
    for janela, similaridade, probabilidades in escolhidas:
        relacao = max(probabilidades, key=probabilidades.get)
        resultado.append(TrechoAvaliado(fonte, janela, similaridade, relacao, probabilidades[relacao], probabilidades))
    return resultado


def rotulos_nli(id2label: dict) -> dict[int, str]:
    # A ordem dos rótulos vem do config de cada modelo; nunca é assumida.
    rotulos = {int(indice): str(nome).lower() for indice, nome in id2label.items()}
    if set(rotulos.values()) != ROTULOS_NLI:
        raise ValueError(f"Rótulos NLI inesperados: {sorted(rotulos.values())}.")
    return rotulos


def com_prefixos(config: dict[str, str], consulta: str, textos: list[str]) -> list[str]:
    # O e5 exige "query: " na consulta e "passage: " nos trechos; na V0 os prefixos são vazios.
    return [config["prefixo_consulta"] + consulta, *[config["prefixo_trecho"] + texto for texto in textos]]


class ModelosHF:
    """Embedding e NLI do Hugging Face para um perfil, carregados na primeira chamada."""

    def __init__(self, nome: str) -> None:
        self.config = perfil(nome)
        self._lock = threading.Lock()
        self._carregados = None

    def _carregar(self):
        if self._carregados is None:
            with self._lock:
                if self._carregados is None:
                    from sentence_transformers import SentenceTransformer
                    from transformers import AutoModelForSequenceClassification, AutoTokenizer

                    embedding = SentenceTransformer(self.config["embedding"], device="cpu")
                    tokenizer = AutoTokenizer.from_pretrained(self.config["nli"])
                    nli = AutoModelForSequenceClassification.from_pretrained(self.config["nli"]).eval()
                    self._carregados = embedding, tokenizer, nli, rotulos_nli(nli.config.id2label)
        return self._carregados

    def similaridades(self, consulta: str, textos: list[str]) -> list[float]:
        embedding, *_ = self._carregar()
        entradas = com_prefixos(self.config, consulta, textos)
        # Os vetores são normalizados, então o produto escalar é a similaridade do cosseno.
        vetores = embedding.encode(entradas, normalize_embeddings=True, convert_to_tensor=True)
        return (vetores[1:] @ vetores[0]).tolist()

    def classificar(self, pares: list[tuple[str, str]]) -> list[dict[str, float]]:
        import torch

        _, tokenizer, nli, rotulos = self._carregar()
        premissas, hipoteses = zip(*pares)
        tokens = tokenizer(list(premissas), list(hipoteses), return_tensors="pt", truncation=True, max_length=512, padding=True)
        with torch.inference_mode():
            probabilidades = torch.softmax(nli(**tokens).logits, dim=-1).tolist()
        return [{rotulos[i]: float(p) for i, p in enumerate(linha)} for linha in probabilidades]


class ComparadorNLI:
    def __init__(self, nome_do_perfil: str | None = None, modelos: Modelos | None = None) -> None:
        self.perfil = nome_perfil(nome_do_perfil)
        self.modelos = modelos or ModelosHF(self.perfil)

    def comparar(self, afirmacao: str, fontes: list[Fonte]) -> list[TrechoAvaliado]:
        pares = candidatos(fontes)
        if not pares:
            return []
        similaridades = self.modelos.similaridades(afirmacao, [janela for _, janela in pares])
        selecionadas = selecionar(
            pares, similaridades, SELECAO["trechos_por_fonte"], SELECAO["max_fontes_por_afirmacao"], LIMIARES["similaridade_selecao"]
        )
        if not selecionadas:
            return []
        entradas = [(janela, afirmacao) for _, itens in selecionadas for janela, _ in itens]
        probabilidades = iter(self.modelos.classificar(entradas))
        resultado: list[TrechoAvaliado] = []
        for fonte, itens in selecionadas:
            resultado.extend(consolidar(fonte, [(janela, similaridade, next(probabilidades)) for janela, similaridade in itens]))
        return resultado
