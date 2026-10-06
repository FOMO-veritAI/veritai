"""Orquestra a análise de uma notícia e monta o relatório por afirmação."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from datetime import date
from typing import TYPE_CHECKING

from .config import CONFIG, versoes
from .copias import agrupar
from .fontes import Fonte, buscar_fontes, dominio_da_fonte
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


if TYPE_CHECKING:
    from .base import BaseEvidencias

Buscador = Callable[[str], Awaitable[tuple[list[Fonte], list[str], list[ChecagemAnterior]]]]

RELACAO = {"entailment": "apoia", "contradiction": "contradiz", "neutral": "neutro"}


def justificativa(agregado: Agregado, encontrou_trechos: bool) -> str:
    a_favor, contra = len(agregado.grupos_a_favor), len(agregado.grupos_contra)
    if agregado.resultado == "SUPPORTED":
        return f"{a_favor} fonte(s) independente(s) trazem trechos que apoiam a afirmação, e nenhuma fonte com relação forte a contradiz."
    if agregado.resultado == "REFUTED":
        return f"{contra} fonte(s) independente(s) trazem trechos que contradizem a afirmação, e nenhuma fonte com relação forte a apoia."
    if agregado.resultado == "CONFLICTING_EVIDENCE":
        return f"{a_favor} fonte(s) apoiam e {contra} fonte(s) contradizem a afirmação."
    if encontrou_trechos:
        return "Os trechos encontrados não têm relação forte o bastante com a afirmação para sustentar um resultado."
    return "Nenhum trecho relacionado à afirmação foi encontrado nas fontes consultadas."


def interpretar_data(valor: str | None) -> tuple[date | None, str | None]:
    """Data da notícia (AAAA-MM-DD) e, se ela veio em outro formato, a limitação correspondente."""
    if not valor:
        return None, None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor.strip()):
        try:
            return date.fromisoformat(valor.strip()), None
        except ValueError:
            pass
    return None, "A data da notícia não está no formato AAAA-MM-DD; as evidências não foram filtradas por data."


def data_da_fonte(fonte: Fonte) -> date | None:
    try:
        return date.fromisoformat((fonte.data_publicacao or "")[:10])
    except ValueError:
        return None


def filtrar_por_data(fontes: list[Fonte], limite: date | None) -> tuple[list[Fonte], int, int]:
    """Remove fontes publicadas depois do dia da notícia; o mesmo dia entra. Devolve (mantidas, removidas, sem data)."""
    if limite is None:
        return fontes, 0, 0
    mantidas, removidas, sem_data = [], 0, 0
    for fonte in fontes:
        dia = data_da_fonte(fonte)
        if dia is None:
            sem_data += 1
        elif dia > limite:
            removidas += 1
            continue
        mantidas.append(fonte)
    return mantidas, removidas, sem_data


def limitacoes(pedido: PedidoAnalise, checagens: list[ChecagemAnterior], usou_base: bool = False, notas: list[str] | None = None) -> list[str]:
    itens = []
    if not CONFIG["calibrado"]:
        itens.append("Porcentagem não exibida: a VeritAI ainda não foi calibrada com dados rotulados.")
    if pedido.buscar_na_web:
        itens.append("A busca usa Bing News e GDELT; a cobertura pode ser incompleta.")
    if not usou_base:
        itens.append("Checagens anteriores ainda não são consultadas em uma base própria.")
    itens.append("O agrupamento de cópias só detecta textos quase literais; matérias reescritas da mesma origem ainda contam como fontes independentes.")
    if checagens:
        itens.append(
            "As checagens anteriores listadas são candidatas encontradas pelo Google Fact Check ou na base própria: a VeritAI ainda não "
            "confirma se tratam da mesma alegação, contexto e período, e elas não definem o resultado. Confira antes de usá-las."
        )
    itens.extend(notas or [])
    if pedido.anexos_sem_texto:
        itens.append("Imagens ou documentos sem texto não foram avaliados pela IA e exigem conferência humana.")
    return itens


def montar_relatorio(
    afirmacao: str,
    itens: list[TrechoAvaliado],
    pedido: PedidoAnalise,
    houve_texto: bool,
    checagens: list[ChecagemAnterior],
    usou_base: bool = False,
    notas: list[str] | None = None,
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
        limitacoes=limitacoes(pedido, checagens, usou_base, notas),
    )


async def analisar(
    pedido: PedidoAnalise, comparador: Comparador, buscador: Buscador = buscar_fontes, base: "BaseEvidencias | None" = None
) -> RelatorioAnalise:
    data_noticia, aviso_data = interpretar_data(pedido.noticia.data)
    fornecidas = [
        Fonte(
            url=item.url,
            titulo=item.titulo,
            dominio=dominio_da_fonte(item.url, item.titulo),
            texto=item.texto,
            origem=item.origem,
            data_publicacao=item.data_publicacao,
        )
        for item in pedido.evidencias
    ]
    with base.leitura() if base is not None else nullcontext() as leitura:
        # Com base própria, toda a análise lê um único snapshot, registrado no relatório.
        snapshot = await asyncio.to_thread(lambda: leitura.snapshot) if leitura is not None else None
        relatorios, avisos = await _analisar_afirmacoes(pedido, comparador, buscador, leitura, fornecidas, data_noticia, aviso_data)
    return RelatorioAnalise(afirmacoes=relatorios, avisos=list(dict.fromkeys(avisos)), versoes=versoes(comparador.perfil, snapshot))


async def _analisar_afirmacoes(
    pedido: PedidoAnalise,
    comparador: Comparador,
    buscador: Buscador,
    base: "BaseEvidencias | None",
    fornecidas: list[Fonte],
    data_noticia: date | None,
    aviso_data: str | None,
) -> tuple[list[RelatorioAfirmacao], list[str]]:
    avisos: list[str] = []
    relatorios = []
    for afirmacao in pedido.afirmacoes:
        encontradas: list[Fonte] = []
        checagens: list[ChecagemAnterior] = []
        if pedido.buscar_na_web:
            encontradas, erros, checagens = await buscador(afirmacao)
            avisos.extend(erros)
        da_base: list[Fonte] = []
        if base is not None:
            da_base = await asyncio.to_thread(base.buscar, afirmacao, data_noticia)
            na_base = await asyncio.to_thread(base.checagens, afirmacao)
            vistas = {(c.url, c.alegacao_checada) for c in checagens}
            checagens = checagens + [c for c in na_base if (c.url, c.alegacao_checada) not in vistas]
        comparaveis, removidas, sem_data = filtrar_por_data(da_base + encontradas + fornecidas, data_noticia)
        # Cópias e páginas do mesmo domínio, de qualquer origem, contam como uma única fonte.
        agrupar(comparaveis)
        itens = await asyncio.to_thread(comparador.comparar, afirmacao, comparaveis)
        houve_texto = bool(candidatos(comparaveis))
        notas = [aviso_data] if aviso_data else []
        if removidas:
            notas.append(f"{removidas} fonte(s) publicadas depois da data da notícia ({data_noticia.isoformat()}) foram descartadas.")
        if sem_data:
            notas.append(f"{sem_data} fonte(s) sem data de publicação entraram na análise; não foi possível confirmar que são anteriores à notícia.")
        relatorios.append(montar_relatorio(afirmacao, itens, pedido, houve_texto, checagens, base is not None, notas))
    return relatorios, avisos
