"""API HTTP da VeritAI, consumida pela plataforma FOMO."""

from fastapi import FastAPI

from .base import BaseEvidencias
from .config import caminho_base, perfil, versoes
from .modelos import Comparador, ComparadorNLI
from .pipeline import Buscador, analisar
from .fontes import buscar_fontes
from .relatorio import PedidoAnalise, RelatorioAnalise


def criar_app(comparador: Comparador | None = None, buscador: Buscador = buscar_fontes, base: BaseEvidencias | None = None) -> FastAPI:
    app = FastAPI(title="VeritAI", version="0.1.0", description="Análise de afirmações factuais com relatório por afirmação.")
    comparador = comparador or ComparadorNLI()
    if base is not None:
        # Falha ao subir, e não na primeira análise, se a base não tiver vetores do perfil em uso.
        base.verificar_indice()

    @app.get("/health")
    def health():
        snapshot = base.snapshot if base is not None else None
        return {"status": "ok", "perfil": comparador.perfil, "versoes": versoes(comparador.perfil, snapshot)}

    @app.post("/analisar", response_model=RelatorioAnalise)
    async def analisar_noticia(pedido: PedidoAnalise):
        return await analisar(pedido, comparador, buscador, base)

    return app


def criar_app_padrao() -> FastAPI:
    """App do serviço: perfil de VERITAI_PERFIL e, se o arquivo existir, a base própria de VERITAI_BASE."""
    comparador = ComparadorNLI()
    caminho = caminho_base()
    embedding = perfil(comparador.perfil)["embedding"]
    base = BaseEvidencias(caminho, embedding, comparador.modelos.vetorizar, comparador.perfil) if caminho.exists() else None
    return criar_app(comparador, buscar_fontes, base)


app = criar_app_padrao()
