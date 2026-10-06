"""Contrato de entrada e saída da VeritAI (relatório por afirmação)."""

from typing import Literal

from pydantic import BaseModel, Field


Resultado = Literal["SUPPORTED", "REFUTED", "NOT_ENOUGH_EVIDENCE", "CONFLICTING_EVIDENCE"]
Relacao = Literal["apoia", "contradiz", "neutro"]
Origem = Literal["busca_web", "checagem_externa", "link_publisher", "anexo", "base_propria"]
MotivoNaoAvaliavel = Literal["modelo_nao_calibrado", "evidencia_insuficiente", "so_anexo_sem_texto"]

TEXTO_PUBLICO: dict[str, str] = {
    "SUPPORTED": "Resultado da verificação VeritAI: as evidências encontradas apoiam esta afirmação.",
    "REFUTED": "Resultado da verificação VeritAI: as evidências encontradas contradizem esta afirmação.",
    "NOT_ENOUGH_EVIDENCE": "Resultado da verificação VeritAI: não encontramos evidências suficientes para avaliar esta afirmação.",
    "CONFLICTING_EVIDENCE": "Resultado da verificação VeritAI: as fontes encontradas apresentam conclusões diferentes sobre esta afirmação.",
}


class EvidenciaFornecida(BaseModel):
    titulo: str = Field(min_length=1, max_length=300)
    texto: str = Field(min_length=1, max_length=40000)
    url: str = ""
    origem: Literal["link_publisher", "anexo"] = "anexo"
    data_publicacao: str | None = None


class Noticia(BaseModel):
    titulo: str = Field(min_length=1, max_length=300)
    texto: str = Field(default="", max_length=30000)
    data: str | None = None


class PedidoAnalise(BaseModel):
    noticia: Noticia
    afirmacoes: list[str] = Field(min_length=1, max_length=10)
    evidencias: list[EvidenciaFornecida] = Field(default_factory=list, max_length=20)
    anexos_sem_texto: int = Field(default=0, ge=0, le=20)
    buscar_na_web: bool = True


class Evidencia(BaseModel):
    fonte: str
    url: str
    data_publicacao: str | None
    trecho: str
    relacao: Relacao
    origem: Origem
    similaridade: float
    pontuacao_nli: float


class ChecagemAnterior(BaseModel):
    agencia: str
    data: str | None
    url: str
    alegacao_checada: str
    veredito: str


class RelatorioAfirmacao(BaseModel):
    afirmacao: str
    resultado: Resultado
    texto_publico: str
    avaliavel: bool
    porcentagem: float | None
    motivo_nao_avaliavel: MotivoNaoAvaliavel | None
    fontes_independentes: int
    evidencias: list[Evidencia]
    checagens_anteriores: list[ChecagemAnterior]
    justificativa: str
    limitacoes: list[str]


class RelatorioAnalise(BaseModel):
    afirmacoes: list[RelatorioAfirmacao]
    avisos: list[str]
    versoes: dict[str, str]
