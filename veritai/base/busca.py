"""Busca híbrida na base própria: BM25 (FTS5) + vetorial exata (numpy), fundidas por RRF."""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import date
from pathlib import Path

import numpy

from ..config import BASE
from ..fontes import Fonte
from ..relatorio import ChecagemAnterior
from .banco import abrir, conectar, snapshot_atual

Vetorizador = Callable[[list[str], str], numpy.ndarray]

PALAVRAS_VAZIAS = {
    "de", "da", "do", "dos", "das", "em", "no", "na", "nos", "nas", "com", "para", "por", "uma", "um", "que", "foi",
    "ser", "será", "vai", "entre", "sobre", "ao", "aos", "as", "os", "se", "mais", "não", "nao", "pelo", "pela",
}


class BaseNaoIndexada(RuntimeError):
    """A base tem trechos sem vetor para o embedding do perfil em uso."""


def consulta_fts(texto: str) -> str | None:
    # Cada termo vai entre aspas para que pontuação ou palavras reservadas do FTS5 não virem operadores.
    termos = [t for t in re.findall(r"\w+", texto.lower()) if len(t) > 2 and t not in PALAVRAS_VAZIAS]
    return " OR ".join(f'"{termo}"' for termo in dict.fromkeys(termos)) or None


def rrf(listas: list[list[int]], k: int | None = None) -> list[int]:
    """Reciprocal Rank Fusion: soma 1 / (k + posição) de cada lista, com posições a partir de 1."""
    k = BASE["rrf_k"] if k is None else k
    pontos: dict[int, float] = {}
    for lista in listas:
        for posicao, item in enumerate(lista, start=1):
            pontos[item] = pontos.get(item, 0.0) + 1 / (k + posicao)
    return sorted(pontos, key=lambda item: (-pontos[item], item))


def titulo_rastreavel(linha) -> str:
    # Sem URL, o título sozinho não distingue documentos; o número permite achar a origem com
    # "python -m veritai.base documento <número>" sem expor caminhos locais no relatório.
    if linha["url"]:
        return linha["titulo"]
    return f"{linha['titulo']} (base própria, documento {linha['documento_id']})"


class BaseEvidencias:
    def __init__(self, caminho: str | Path, embedding: str, vetorizar: Vetorizador, perfil: str = "<perfil em uso>") -> None:
        self.caminho = Path(caminho)
        self.embedding = embedding
        self.vetorizar = vetorizar
        self.perfil = perfil
        self._fixa = None  # conexão de uma leitura em andamento (ver leitura())

    def _conectar(self):
        return nullcontext(self._fixa) if self._fixa is not None else abrir(self.caminho)

    @contextmanager
    def leitura(self) -> Iterator["BaseEvidencias"]:
        """Visão fixa da base para uma análise inteira: snapshot, buscas e checagens leem o mesmo estado,
        mesmo que uma carga seja gravada no meio (o banco usa WAL)."""
        conexao = conectar(self.caminho)
        try:
            conexao.execute("BEGIN")
            visao = BaseEvidencias(self.caminho, self.embedding, self.vetorizar, self.perfil)
            visao._fixa = conexao
            yield visao
        finally:
            conexao.rollback()
            conexao.close()

    @property
    def snapshot(self) -> str | None:
        with self._conectar() as conexao:
            return snapshot_atual(conexao)

    def verificar_indice(self) -> None:
        """Falha se algum trecho não tem vetor do embedding em uso: vetores de perfis diferentes nunca se misturam."""
        with self._conectar() as conexao:
            faltando = conexao.execute(
                "SELECT COUNT(*) FROM trechos t LEFT JOIN vetores v ON v.trecho = t.id AND v.embedding = ? WHERE v.trecho IS NULL",
                (self.embedding,),
            ).fetchone()[0]
            existentes = [linha[0] for linha in conexao.execute("SELECT DISTINCT embedding FROM vetores")]
        if faltando:
            indexada = f" A base tem vetores de: {', '.join(existentes)}." if existentes else ""
            raise BaseNaoIndexada(
                f"{faltando} trecho(s) da base própria não têm vetor para o embedding {self.embedding}.{indexada} "
                f"Rode: python -m veritai.base indexar --perfil {self.perfil}"
            )

    @staticmethod
    def _filtro_data(ate: date | None) -> tuple[str, list[str]]:
        # Documento sem data entra; o pipeline registra isso nas limitações.
        if ate is None:
            return "", []
        return "AND (d.data_publicacao IS NULL OR substr(d.data_publicacao, 1, 10) <= ?)", [ate.isoformat()]

    def buscar_bm25(self, afirmacao: str, ate: date | None = None, limite: int | None = None) -> list[int]:
        consulta = consulta_fts(afirmacao)
        if not consulta:
            return []
        filtro, parametros = self._filtro_data(ate)
        with self._conectar() as conexao:
            return [
                linha[0]
                for linha in conexao.execute(
                    f"""SELECT t.id FROM trechos_fts f JOIN trechos t ON t.id = f.rowid JOIN documentos d ON d.id = t.documento
                    WHERE trechos_fts MATCH ? {filtro} ORDER BY bm25(trechos_fts), t.id LIMIT ?""",
                    [consulta, *parametros, limite or BASE["trechos_por_busca"]],
                )
            ]

    def buscar_vetorial(self, afirmacao: str, ate: date | None = None, limite: int | None = None) -> list[int]:
        self.verificar_indice()
        filtro, parametros = self._filtro_data(ate)
        with self._conectar() as conexao:
            linhas = conexao.execute(
                f"""SELECT v.trecho, v.vetor FROM vetores v JOIN trechos t ON t.id = v.trecho JOIN documentos d ON d.id = t.documento
                WHERE v.embedding = ? {filtro} ORDER BY v.trecho""",
                [self.embedding, *parametros],
            ).fetchall()
        if not linhas:
            return []
        matriz = numpy.vstack([numpy.frombuffer(linha["vetor"], dtype=numpy.float32) for linha in linhas])
        consulta = numpy.asarray(self.vetorizar([afirmacao], "consulta"), dtype=numpy.float32)[0]
        # Vetores normalizados: o produto escalar é a similaridade do cosseno. Busca exata, sem índice aproximado.
        pontos = matriz @ consulta
        return [linhas[i]["trecho"] for i in numpy.argsort(-pontos, kind="stable")[: limite or BASE["trechos_por_busca"]]]

    def buscar(self, afirmacao: str, ate: date | None = None) -> list[Fonte]:
        """Os trechos mais relevantes pela fusão RRF, como fontes de origem base_propria."""
        limite = BASE["trechos_por_busca"]
        escolhidos = rrf([self.buscar_bm25(afirmacao, ate), self.buscar_vetorial(afirmacao, ate)])[:limite]
        if not escolhidos:
            return []
        marcadores = ",".join("?" * len(escolhidos))
        with self._conectar() as conexao:
            dados = {
                linha["id"]: linha
                for linha in conexao.execute(
                    f"""SELECT t.id, t.texto, d.id AS documento_id, d.url, d.titulo, d.dominio, d.data_publicacao, d.grupo_copias, d.texto AS documento
                    FROM trechos t JOIN documentos d ON d.id = t.documento WHERE t.id IN ({marcadores})""",
                    escolhidos,
                )
            }
        return [
            Fonte(
                url=dados[i]["url"],
                titulo=titulo_rastreavel(dados[i]),
                dominio=dados[i]["dominio"],
                texto=dados[i]["texto"],
                origem="base_propria",
                data_publicacao=dados[i]["data_publicacao"],
                texto_completo=dados[i]["documento"],
                copia_de=f"base:{dados[i]['grupo_copias']}",
            )
            for i in escolhidos
        ]

    def checagens(self, afirmacao: str) -> list[ChecagemAnterior]:
        """Checagens mais parecidas pela alegação (BM25), com o veredito original da agência."""
        consulta = consulta_fts(afirmacao)
        if not consulta:
            return []
        with self._conectar() as conexao:
            linhas = conexao.execute(
                """SELECT c.agencia, c.data, c.url, c.alegacao, c.veredito_original FROM checagens_fts f
                JOIN checagens c ON c.id = f.rowid WHERE checagens_fts MATCH ? ORDER BY bm25(checagens_fts) LIMIT ?""",
                (consulta, BASE["checagens_por_afirmacao"]),
            ).fetchall()
        return [
            ChecagemAnterior(agencia=l["agencia"], data=l["data"], url=l["url"], alegacao_checada=l["alegacao"], veredito=l["veredito_original"])
            for l in linhas
        ]
