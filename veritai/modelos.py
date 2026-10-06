"""Comparação afirmação × trechos por similaridade semântica e NLI (versão V0).

Os modelos só são carregados na primeira análise, para que os testes e a API
subam sem baixar pesos.
"""

import re
import threading
from dataclasses import dataclass
from typing import Protocol

from .config import CONFIG, LIMIARES
from .fontes import Fonte


@dataclass
class TrechoAvaliado:
    fonte: Fonte
    trecho: str
    similaridade: float
    relacao: str  # entailment | contradiction | neutral
    pontuacao_nli: float


class Comparador(Protocol):
    def comparar(self, afirmacao: str, fontes: list[Fonte]) -> list[TrechoAvaliado]: ...


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


def candidatos(fontes: list[Fonte]) -> list[tuple[Fonte, str]]:
    return [(fonte, trecho) for fonte in fontes for trecho in dividir_em_trechos(fonte.texto, fonte.origem in ORIGENS_FORNECIDAS)]


class ComparadorNLI:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._modelos = None

    def _carregar(self):
        if self._modelos is None:
            with self._lock:
                if self._modelos is None:
                    from sentence_transformers import SentenceTransformer
                    from transformers import AutoModelForSequenceClassification, AutoTokenizer

                    embedding = SentenceTransformer(CONFIG["modelos"]["embedding"], device="cpu")
                    tokenizer = AutoTokenizer.from_pretrained(CONFIG["modelos"]["nli"])
                    nli = AutoModelForSequenceClassification.from_pretrained(CONFIG["modelos"]["nli"]).eval()
                    self._modelos = embedding, tokenizer, nli
        return self._modelos

    def comparar(self, afirmacao: str, fontes: list[Fonte]) -> list[TrechoAvaliado]:
        pares = candidatos(fontes)
        if not pares:
            return []
        import torch

        embedding, tokenizer, nli = self._carregar()
        # Os vetores são normalizados, então o produto escalar é a similaridade do cosseno.
        vetores = embedding.encode([afirmacao, *[texto for _, texto in pares]], normalize_embeddings=True, convert_to_tensor=True)
        similaridades = (vetores[1:] @ vetores[0]).tolist()
        melhor_por_dominio: dict[str, tuple[Fonte, str, float]] = {}
        for (fonte, trecho), similaridade in zip(pares, similaridades):
            if similaridade < LIMIARES["similaridade_selecao"]:
                continue
            anterior = melhor_por_dominio.get(fonte.dominio)
            if anterior is None or similaridade > anterior[2]:
                melhor_por_dominio[fonte.dominio] = fonte, trecho, float(similaridade)
        selecionados = sorted(melhor_por_dominio.values(), key=lambda item: item[2], reverse=True)[: LIMIARES["max_trechos_por_afirmacao"]]
        resultado: list[TrechoAvaliado] = []
        for fonte, trecho, similaridade in selecionados:
            tokens = tokenizer(trecho, afirmacao, return_tensors="pt", truncation=True, max_length=512)
            with torch.inference_mode():
                probabilidades = torch.softmax(nli(**tokens).logits[0], dim=-1).tolist()
            rotulos = {str(nli.config.id2label[i]).lower(): p for i, p in enumerate(probabilidades)}
            relacao = max(rotulos, key=rotulos.get)
            if relacao not in {"entailment", "contradiction", "neutral"}:
                relacao = "neutral"
            resultado.append(TrechoAvaliado(fonte, trecho, similaridade, relacao, float(rotulos.get(relacao, 0))))
        return resultado
