"""Carga da base própria: documentos de URLs ou de texto local, checagens e vetores.

Não há coleta em massa: as URLs vêm de um arquivo escrito pelo grupo, com limite
por carga, e cada página passa pela checagem de URL pública e de tamanho.
"""

import asyncio
import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy

from ..config import BASE
from ..copias import sao_copias, shingles
from ..fontes import Fonte, checagens_do_google, dominio_da_fonte, google_fact_check, ler_pagina
from ..relatorio import ChecagemAnterior
from . import trechos
from .banco import abrir, agora, nova_carga


@dataclass
class ResultadoCarga:
    carga: str | None = None
    inseridos: int = 0
    repetidos: int = 0
    erros: list[str] = field(default_factory=list)


def data_iso(valor: str | None) -> str | None:
    """AAAA-MM-DD a partir do início do texto, ou None se não for uma data legível."""
    try:
        return date.fromisoformat((valor or "")[:10]).isoformat()
    except ValueError:
        return None


def _grupo_de_copias(conexao: sqlite3.Connection, texto: str) -> int | None:
    # Comparação exaustiva com os documentos já carregados; suficiente enquanto a base for pequena.
    novo = shingles(texto)
    for linha in conexao.execute("SELECT texto, grupo_copias FROM documentos ORDER BY id"):
        if sao_copias(novo, shingles(linha["texto"])):
            return linha["grupo_copias"]
    return None


def _inserir_documento(conexao: sqlite3.Connection, carga: str, fonte: Fonte, tipo_origem: str, referencia: str) -> bool:
    """Insere uma publicação. Só a mesma referência (URL ou arquivo) com o mesmo texto conta como repetida."""
    hash_conteudo = hashlib.sha256(fonte.texto.encode("utf-8")).hexdigest()
    if conexao.execute("SELECT 1 FROM documentos WHERE referencia = ? AND hash_conteudo = ?", (referencia, hash_conteudo)).fetchone():
        return False
    grupo = _grupo_de_copias(conexao, fonte.texto)
    cursor = conexao.execute(
        """INSERT INTO documentos (url, referencia, dominio, titulo, tipo_origem, data_publicacao, data_coleta, hash_conteudo, grupo_copias, texto, carga)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (fonte.url, referencia, fonte.dominio, fonte.titulo, tipo_origem, data_iso(fonte.data_publicacao), agora(), hash_conteudo, fonte.texto, carga),
    )
    documento = cursor.lastrowid
    conexao.execute("UPDATE documentos SET grupo_copias = ? WHERE id = ?", (grupo or documento, documento))
    for posicao, texto in enumerate(trechos.dividir(fonte.texto)):
        trecho = conexao.execute("INSERT INTO trechos (documento, posicao, texto) VALUES (?, ?, ?)", (documento, posicao, texto)).lastrowid
        conexao.execute("INSERT INTO trechos_fts (rowid, texto) VALUES (?, ?)", (trecho, texto))
    return True


def _carregar(caminho: Path, comando: str, documentos: list[tuple[Fonte, str, str]], erros: list[str]) -> ResultadoCarga:
    resultado = ResultadoCarga(erros=erros)
    with abrir(caminho, criar=True) as conexao:
        carga = nova_carga(conexao, comando)
        for fonte, tipo_origem, referencia in documentos:
            if _inserir_documento(conexao, carga, fonte, tipo_origem, referencia):
                resultado.inseridos += 1
            else:
                resultado.repetidos += 1
        # Uma carga sem conteúdo novo não gera snapshot.
        if resultado.inseridos:
            resultado.carga = carga
        else:
            conexao.execute("DELETE FROM cargas WHERE id = ?", (carga,))
    return resultado


def importar_urls(caminho: Path, arquivo: Path, leitor: Callable | None = None) -> ResultadoCarga:
    leitor = leitor or ler_pagina
    urls = [linha.strip() for linha in arquivo.read_text(encoding="utf-8").splitlines() if linha.strip() and not linha.lstrip().startswith("#")]
    urls = list(dict.fromkeys(urls))
    if len(urls) > BASE["max_urls_por_carga"]:
        raise ValueError(f"O arquivo tem {len(urls)} URLs; o limite por carga é {BASE['max_urls_por_carga']}.")

    async def ler_todas():
        documentos, erros = [], []
        # Uma página por vez: a base é montada a partir de uma lista curada, não por raspagem.
        for url in urls:
            try:
                documentos.append((await leitor(url), "url", url))
            except Exception as exc:
                erros.append(f"{url}: {type(exc).__name__}: {exc}")
        return documentos, erros

    documentos, erros = asyncio.run(ler_todas())
    return _carregar(caminho, f"importar-urls {arquivo.name}", documentos, erros)


def importar_textos(caminho: Path, arquivos: list[Path], data_publicacao: str | None = None) -> ResultadoCarga:
    if data_publicacao and not data_iso(data_publicacao):
        raise ValueError("A data deve estar no formato AAAA-MM-DD.")
    documentos, erros = [], []
    for arquivo in arquivos:
        texto = arquivo.read_text(encoding="utf-8").strip()
        if not texto:
            erros.append(f"{arquivo}: arquivo vazio")
            continue
        # O caminho completo identifica o arquivo: arquivos de pastas diferentes com o mesmo nome são fontes distintas.
        referencia = str(arquivo.resolve())
        fonte = Fonte(url="", titulo=arquivo.stem, dominio=dominio_da_fonte("", referencia), texto=texto, origem="base_propria", data_publicacao=data_publicacao)
        documentos.append((fonte, "texto_local", referencia))
    return _carregar(caminho, f"importar-textos {len(arquivos)} arquivo(s)", documentos, erros)


def importar_checagens(caminho: Path, checagens: list[ChecagemAnterior], comando: str) -> ResultadoCarga:
    """Grava o veredito original da agência; o mapeamento para as 4 classes não é feito aqui."""
    resultado = ResultadoCarga()
    with abrir(caminho, criar=True) as conexao:
        carga = nova_carga(conexao, comando)
        for checagem in checagens:
            cursor = conexao.execute(
                "INSERT OR IGNORE INTO checagens (alegacao, veredito_original, agencia, data, url, carga) VALUES (?, ?, ?, ?, ?, ?)",
                (checagem.alegacao_checada, checagem.veredito, checagem.agencia, data_iso(checagem.data), checagem.url, carga),
            )
            if cursor.rowcount:
                conexao.execute("INSERT INTO checagens_fts (rowid, alegacao) VALUES (?, ?)", (cursor.lastrowid, checagem.alegacao_checada))
                resultado.inseridos += 1
            else:
                resultado.repetidos += 1
        if resultado.inseridos:
            resultado.carga = carga
        else:
            conexao.execute("DELETE FROM cargas WHERE id = ?", (carga,))
    return resultado


def checagens_de_arquivo(arquivo: Path) -> list[ChecagemAnterior]:
    """Arquivo JSON no formato da resposta de claims:search do Google Fact Check (lista em "claims")."""
    import json

    return checagens_do_google(json.loads(arquivo.read_text(encoding="utf-8")))


def checagens_de_consulta(consulta: str) -> list[ChecagemAnterior]:
    return asyncio.run(google_fact_check(consulta))


def indexar(caminho: Path, embedding: str, vetorizar: Callable[[list[str], str], numpy.ndarray], lote: int = 64) -> int:
    """Calcula vetores dos trechos que ainda não têm vetor para este embedding."""
    total = 0
    with abrir(caminho) as conexao:
        pendentes = conexao.execute(
            "SELECT t.id, t.texto FROM trechos t LEFT JOIN vetores v ON v.trecho = t.id AND v.embedding = ? WHERE v.trecho IS NULL ORDER BY t.id",
            (embedding,),
        ).fetchall()
        for inicio in range(0, len(pendentes), lote):
            parte = pendentes[inicio : inicio + lote]
            vetores = numpy.asarray(vetorizar([linha["texto"] for linha in parte], "trecho"), dtype=numpy.float32)
            conexao.executemany(
                "INSERT INTO vetores (trecho, embedding, dimensao, vetor) VALUES (?, ?, ?, ?)",
                [(linha["id"], embedding, vetor.shape[0], vetor.tobytes()) for linha, vetor in zip(parte, vetores)],
            )
            total += len(parte)
    return total


def documento(caminho: Path, identificador: int) -> dict | None:
    """Metadados de um documento da base, para conferir de onde veio uma evidência."""
    with abrir(caminho) as conexao:
        linha = conexao.execute(
            """SELECT id, url, referencia, dominio, titulo, tipo_origem, data_publicacao, data_coleta, hash_conteudo, grupo_copias, carga,
            (SELECT COUNT(*) FROM trechos WHERE documento = documentos.id) AS trechos FROM documentos WHERE id = ?""",
            (identificador,),
        ).fetchone()
    return dict(linha) if linha else None


def estatisticas(caminho: Path) -> dict:
    with abrir(caminho) as conexao:
        um = lambda sql: conexao.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "snapshot": um("SELECT id FROM cargas ORDER BY criada_em DESC, rowid DESC LIMIT 1"),
            "cargas": um("SELECT COUNT(*) FROM cargas"),
            "documentos": um("SELECT COUNT(*) FROM documentos"),
            "grupos_de_copias": um("SELECT COUNT(DISTINCT grupo_copias) FROM documentos"),
            "dominios": um("SELECT COUNT(DISTINCT dominio) FROM documentos"),
            "documentos_sem_data": um("SELECT COUNT(*) FROM documentos WHERE data_publicacao IS NULL"),
            "trechos": um("SELECT COUNT(*) FROM trechos"),
            "checagens": um("SELECT COUNT(*) FROM checagens"),
            "vetores_por_embedding": dict(conexao.execute("SELECT embedding, COUNT(*) FROM vetores GROUP BY embedding").fetchall()),
        }
