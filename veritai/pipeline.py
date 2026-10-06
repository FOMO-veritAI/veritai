"""Orquestra a análise de uma notícia e monta o relatório por afirmação."""

import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from .config import CONFIG, versoes
from .fontes import Fonte, buscar_fontes
from .modelos import Comparador, TrechoAvaliado, candidatos
from .regras import Agregado, agregar
from .relatorio import (
    TEXTO_PUBLICO,
    ChecagemAnterior,
    Evidencia,
    PedidoAnalise,
    RelatorioAfirmacao,
    RelatorioAnalise,
)


Buscador = Callable[[str], Awaitable[tuple[list[Fonte], list[str], list[ChecagemAnterior]]]]

RELACAO = {"entailment": "apoia", "contradiction": "contradiz", "neutral": "neutro"}


def justificativa(agregado: Agregado, encontrou_trechos: bool) -> str:
    a_favor, contra = len(agregado.dominios_a_favor), len(agregado.dominios_contra)
    if agregado.resultado == "SUPPORTED":
        return f"{a_favor} fonte(s) independente(s) trazem trechos que apoiam a afirmação, e nenhuma fonte com relação forte a contradiz."
    if agregado.resultado == "REFUTED":
        return f"{contra} fonte(s) independente(s) trazem trechos que contradizem a afirmação, e nenhuma fonte com relação forte a apoia."
    if agregado.resultado == "CONFLICTING_EVIDENCE":
        return f"{a_favor} fonte(s) apoiam e {contra} fonte(s) contradizem a afirmação."
    if encontrou_trechos:
        return "Os trechos encontrados não têm relação forte o bastante com a afirmação para sustentar um resultado."
    return "Nenhum trecho relacionado à afirmação foi encontrado nas fontes consultadas."


def limitacoes(pedido: PedidoAnalise, checagens: list[ChecagemAnterior]) -> list[str]:
    itens = []
    if not CONFIG["calibrado"]:
        itens.append("Porcentagem não exibida: a VeritAI ainda não foi calibrada com dados rotulados.")
    if pedido.buscar_na_web:
        itens.append("A busca usa Bing News e GDELT; a cobertura pode ser incompleta.")
    itens.append("Checagens anteriores ainda não são consultadas em uma base própria.")
    if checagens:
        itens.append(
            "As checagens anteriores listadas são candidatas encontradas pelo Google Fact Check: a VeritAI ainda não confirma se tratam "
            "da mesma alegação, contexto e período, e elas não definem o resultado. Confira antes de usá-las."
        )
    if pedido.anexos_sem_texto:
        itens.append("Imagens ou documentos sem texto não foram avaliados pela IA e exigem conferência humana.")
    return itens


def montar_relatorio(
    afirmacao: str,
    itens: list[TrechoAvaliado],
    pedido: PedidoAnalise,
    houve_texto: bool,
    checagens: list[ChecagemAnterior],
) -> RelatorioAfirmacao:
    agregado = agregar(itens)
    if agregado.resultado == "NOT_ENOUGH_EVIDENCE":
        motivo = "so_anexo_sem_texto" if pedido.anexos_sem_texto and not houve_texto else "evidencia_insuficiente"
    else:
        motivo = "modelo_nao_calibrado"
    # Sem calibração não existe porcentagem válida; nenhum número é inventado.
    porcentagem = None
    avaliavel = CONFIG["calibrado"] and agregado.resultado != "NOT_ENOUGH_EVIDENCE" and porcentagem is not None
    return RelatorioAfirmacao(
        afirmacao=afirmacao,
        resultado=agregado.resultado,
        texto_publico=TEXTO_PUBLICO[agregado.resultado],
        avaliavel=avaliavel,
        porcentagem=porcentagem,
        motivo_nao_avaliavel=None if avaliavel else motivo,
        fontes_independentes=agregado.fontes_independentes,
        evidencias=[
            Evidencia(
                fonte=item.fonte.titulo,
                url=item.fonte.url,
                data_publicacao=item.fonte.data_publicacao,
                trecho=item.trecho,
                relacao=RELACAO[item.relacao],
                origem=item.fonte.origem,
                similaridade=round(item.similaridade, 4),
                pontuacao_nli=round(item.pontuacao_nli, 4),
            )
            for item in itens
        ],
        checagens_anteriores=checagens,
        justificativa=justificativa(agregado, bool(itens)),
        limitacoes=limitacoes(pedido, checagens),
    )


async def analisar(pedido: PedidoAnalise, comparador: Comparador, buscador: Buscador = buscar_fontes) -> RelatorioAnalise:
    fornecidas = [
        Fonte(
            url=item.url,
            titulo=item.titulo,
            # Documentos sem URL viram uma fonte própria, identificada pelo título.
            dominio=urlparse(item.url).hostname or f"documento:{item.titulo}",
            texto=item.texto,
            origem=item.origem,
            data_publicacao=item.data_publicacao,
        )
        for item in pedido.evidencias
    ]
    avisos: list[str] = []
    relatorios = []
    for afirmacao in pedido.afirmacoes:
        encontradas: list[Fonte] = []
        checagens: list[ChecagemAnterior] = []
        if pedido.buscar_na_web:
            encontradas, erros, checagens = await buscador(afirmacao)
            avisos.extend(erros)
        comparaveis = encontradas + fornecidas
        itens = await asyncio.to_thread(comparador.comparar, afirmacao, comparaveis)
        houve_texto = bool(candidatos(comparaveis))
        relatorios.append(montar_relatorio(afirmacao, itens, pedido, houve_texto, checagens))
    return RelatorioAnalise(afirmacoes=relatorios, avisos=list(dict.fromkeys(avisos)), versoes=versoes())
