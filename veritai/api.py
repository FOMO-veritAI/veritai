"""API HTTP da VeritAI, consumida pela plataforma FOMO."""

from fastapi import FastAPI

from .config import versoes
from .modelos import Comparador, ComparadorNLI
from .pipeline import Buscador, analisar
from .fontes import buscar_fontes
from .relatorio import PedidoAnalise, RelatorioAnalise


def criar_app(comparador: Comparador | None = None, buscador: Buscador = buscar_fontes) -> FastAPI:
    app = FastAPI(title="VeritAI", version="0.1.0", description="Análise de afirmações factuais com relatório por afirmação.")
    comparador = comparador or ComparadorNLI()

    @app.get("/health")
    def health():
        return {"status": "ok", "versoes": versoes()}

    @app.post("/analisar", response_model=RelatorioAnalise)
    async def analisar_noticia(pedido: PedidoAnalise):
        return await analisar(pedido, comparador, buscador)

    return app


app = criar_app()
