"""Contrato de entrada e saída da VeritAI (relatório por afirmação)."""

import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


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


def host_valido(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    rotulos = ascii_host.removesuffix(".").split(".")
    return len(ascii_host) <= 253 and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", rotulo, re.IGNORECASE) for rotulo in rotulos)


class EvidenciaFornecida(BaseModel):
    titulo: str = Field(min_length=1, max_length=300)
    texto: str = Field(min_length=1, max_length=40000)
    url: str = Field(default="", max_length=2048)
    origem: Literal["link_publisher", "anexo"] = "anexo"
    data_publicacao: str | None = None

    @field_validator("url")
    @classmethod
    def url_valida(cls, url: str) -> str:
        url = url.strip()
        if not url:
            return url
        if any(caractere.isspace() for caractere in url):
            raise ValueError("A URL não pode conter espaços.")
        try:
            partes = urlparse(url)
            hostname, _ = partes.hostname, partes.port
        except ValueError as exc:
            raise ValueError("URL inválida.") from exc
        if partes.scheme not in {"https", "http"} or not hostname or not host_valido(hostname):
            raise ValueError("A URL precisa começar com http:// ou https:// e ter um domínio.")
        return url


class Noticia(BaseModel):
    titulo: str = Field(min_length=1, max_length=300)
    texto: str = Field(default="", max_length=30000)
    data: str | None = None


class PedidoAnalise(BaseModel):
    noticia: Noticia
    afirmacoes: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]] = Field(min_length=1, max_length=10)
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

    @model_validator(mode="after")
    def avaliavel_exige_porcentagem(self):
        # Trava: nenhum resultado é marcado como avaliável sem uma porcentagem calibrada.
        if self.avaliavel and self.porcentagem is None:
            raise ValueError("avaliavel só pode ser true quando existe porcentagem.")
        return self


class RelatorioAnalise(BaseModel):
    afirmacoes: list[RelatorioAfirmacao]
    avisos: list[str]
    versoes: dict[str, str]
