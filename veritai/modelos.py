"""Comparação afirmação × trechos por similaridade semântica e NLI.

O perfil de modelos (v0, v1) vem do config. Os modelos só são carregados na
primeira análise, para que os testes e a API subam sem baixar pesos.
"""

import re
import threading
import time

import numpy
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

    def vetorizar(self, textos: list[str], tipo: str) -> "numpy.ndarray": ...

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
        chave = fonte.grupo or fonte.dominio
        (repetidas if chave in dominios else primeiras).append((fonte, itens))
        dominios.add(chave)
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

    def vetorizar(self, textos: list[str], tipo: str) -> numpy.ndarray:
        """Vetores normalizados (float32) para a base própria; tipo é "consulta" ou "trecho"."""
        embedding, *_ = self._carregar()
        prefixo = self.config["prefixo_consulta"] if tipo == "consulta" else self.config["prefixo_trecho"]
        vetores = embedding.encode([prefixo + texto for texto in textos], normalize_embeddings=True, convert_to_numpy=True)
        return numpy.asarray(vetores, dtype=numpy.float32)

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

    def comparar(self, afirmacao: str, fontes: list[Fonte], rastro: dict | None = None) -> list[TrechoAvaliado]:
        """Compara a afirmação com as fontes. Com rastro (uso interno, ex.: demonstração), registra cada etapa;
        o resultado é o mesmo com ou sem rastro."""
        relogio = time.perf_counter
        tempos: dict[str, float] = {}
        inicio = relogio()
        pares = candidatos(fontes)
        tempos["divisao_em_janelas"] = relogio() - inicio
        similaridades: list[float] = []
        selecionadas: list[tuple[Fonte, list[tuple[str, float]]]] = []
        avaliadas: dict[tuple[int, str], list[dict[str, float]]] = {}
        resultado: list[TrechoAvaliado] = []
        if pares:
            inicio = relogio()
            similaridades = self.modelos.similaridades(afirmacao, [janela for _, janela in pares])
            tempos["similaridade"] = relogio() - inicio
            inicio = relogio()
            selecionadas = selecionar(
                pares, similaridades, SELECAO["trechos_por_fonte"], SELECAO["max_fontes_por_afirmacao"], LIMIARES["similaridade_selecao"]
            )
            tempos["selecao"] = relogio() - inicio
        if selecionadas:
            entradas = [(janela, afirmacao) for _, itens in selecionadas for janela, _ in itens]
            inicio = relogio()
            probabilidades = self.modelos.classificar(entradas)
            tempos["nli"] = relogio() - inicio
            inicio = relogio()
            restantes = iter(probabilidades)
            for fonte, itens in selecionadas:
                com_nli = [(janela, similaridade, next(restantes)) for janela, similaridade in itens]
                for janela, _, nli in com_nli:
                    avaliadas.setdefault((id(fonte), janela), []).append(nli)
                resultado.extend(consolidar(fonte, com_nli))
            tempos["consolidacao"] = relogio() - inicio
        if rastro is not None:
            rastro.update(montar_rastro(self.perfil, fontes, pares, similaridades, avaliadas, resultado, tempos))
        return resultado


def montar_rastro(
    nome_do_perfil: str,
    fontes: list[Fonte],
    pares: list[tuple[Fonte, str]],
    similaridades: list[float],
    avaliadas: dict[tuple[int, str], list[dict[str, float]]],
    resultado: list[TrechoAvaliado],
    tempos: dict[str, float],
) -> dict:
    # Uma janela pode se repetir na mesma fonte. A seleção é estável, então as ocorrências levadas ao NLI
    # (e as que ficaram no relatório) são as primeiras de cada texto: marca-se só essa quantidade, por posição.
    pendentes_nli = {chave: list(lista) for chave, lista in avaliadas.items()}
    pendentes_relatorio: dict[tuple[int, str], int] = {}
    for item in resultado:
        chave = (id(item.fonte), item.trecho)
        pendentes_relatorio[chave] = pendentes_relatorio.get(chave, 0) + 1
    por_fonte = []
    for fonte in fontes:
        fornecido = fonte.origem in ORIGENS_FORNECIDAS
        trechos = dividir_em_trechos(fonte.texto, fornecido)
        por_fonte.append({
            "titulo": fonte.titulo,
            "divisao": "frases de 45 a 800 caracteres" if not fornecido else "partes de até 650 caracteres (frases curtas agrupadas)",
            "url": fonte.url,
            "dominio": fonte.dominio,
            "grupo": fonte.grupo or fonte.dominio,
            "origem": fonte.origem,
            "data_publicacao": fonte.data_publicacao,
            "trechos": trechos,
            "janelas": [],
        })
    posicao = {id(fonte): i for i, fonte in enumerate(fontes)}
    contagem = [0] * len(fontes)
    for indice, (fonte, janela) in enumerate(pares):
        i = posicao[id(fonte)]
        # janelas() lista primeiro as de uma parte (um trecho) e depois as de duas partes consecutivas.
        partes = 1 if contagem[i] < len(por_fonte[i]["trechos"]) else 2
        contagem[i] += 1
        chave = (id(fonte), janela)
        nli = pendentes_nli[chave].pop(0) if pendentes_nli.get(chave) else None
        no_relatorio = nli is not None and pendentes_relatorio.get(chave, 0) > 0
        if no_relatorio:
            pendentes_relatorio[chave] -= 1
        por_fonte[i]["janelas"].append({
            "texto": janela,
            "partes": partes,
            "frases": len(re.findall(r"[^.!?]+[.!?]*", janela.strip())),
            "similaridade": similaridades[indice] if indice < len(similaridades) else None,
            "levada_ao_nli": nli is not None,
            "nli": nli,
            "no_relatorio": no_relatorio,
        })
    return {
        "perfil": nome_do_perfil,
        "parametros": {
            "similaridade_selecao": LIMIARES["similaridade_selecao"],
            "trechos_por_fonte": SELECAO["trechos_por_fonte"],
            "max_fontes_por_afirmacao": SELECAO["max_fontes_por_afirmacao"],
        },
        "fontes": por_fonte,
        "tempos_s": tempos,
    }
