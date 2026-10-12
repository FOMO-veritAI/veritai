"""Esquema e conexão do banco SQLite da base própria. O esquema está descrito em README.md."""

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


ESQUEMA = """
CREATE TABLE IF NOT EXISTS cargas (
    id TEXT PRIMARY KEY,
    criada_em TEXT NOT NULL,
    comando TEXT NOT NULL,
    detalhes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL DEFAULT '',
    referencia TEXT NOT NULL,
    dominio TEXT NOT NULL,
    titulo TEXT NOT NULL,
    tipo_origem TEXT NOT NULL CHECK (tipo_origem IN ('url', 'texto_local')),
    data_publicacao TEXT,
    data_coleta TEXT NOT NULL,
    hash_conteudo TEXT NOT NULL,
    grupo_copias INTEGER NOT NULL,
    texto TEXT NOT NULL,
    carga TEXT NOT NULL REFERENCES cargas(id),
    periodo_referencia TEXT,
    -- Cada publicação é um documento: o mesmo texto em outra URL ou arquivo entra, no mesmo grupo de cópias.
    UNIQUE (referencia, hash_conteudo)
);
CREATE TABLE IF NOT EXISTS trechos (
    id INTEGER PRIMARY KEY,
    documento INTEGER NOT NULL REFERENCES documentos(id),
    posicao INTEGER NOT NULL,
    texto TEXT NOT NULL,
    UNIQUE (documento, posicao)
);
CREATE VIRTUAL TABLE IF NOT EXISTS trechos_fts USING fts5(
    texto, content='trechos', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS vetores (
    trecho INTEGER NOT NULL REFERENCES trechos(id),
    embedding TEXT NOT NULL,
    dimensao INTEGER NOT NULL,
    vetor BLOB NOT NULL,
    PRIMARY KEY (trecho, embedding)
);
CREATE TABLE IF NOT EXISTS checagens (
    id INTEGER PRIMARY KEY,
    alegacao TEXT NOT NULL,
    veredito_original TEXT NOT NULL,
    agencia TEXT NOT NULL,
    data TEXT,
    url TEXT NOT NULL,
    carga TEXT NOT NULL REFERENCES cargas(id),
    classe TEXT CHECK (classe IN ('SUPPORTED', 'REFUTED', 'NOT_ENOUGH_EVIDENCE', 'CONFLICTING_EVIDENCE')),
    origem_classe TEXT,
    UNIQUE (url, alegacao)
);
CREATE VIRTUAL TABLE IF NOT EXISTS checagens_fts USING fts5(
    alegacao, content='checagens', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
"""


VERSAO_ESQUEMA = 2

# Passos de uma versão do esquema para a seguinte, aplicados só pelo comando "migrar".
MIGRACOES = {
    1: """
    ALTER TABLE documentos ADD COLUMN periodo_referencia TEXT;
    ALTER TABLE checagens ADD COLUMN classe TEXT CHECK (classe IN ('SUPPORTED', 'REFUTED', 'NOT_ENOUGH_EVIDENCE', 'CONFLICTING_EVIDENCE'));
    ALTER TABLE checagens ADD COLUMN origem_classe TEXT;
    """,
}


def conectar(caminho: str | Path, criar: bool = False) -> sqlite3.Connection:
    caminho = Path(caminho)
    if not criar and not caminho.exists():
        raise FileNotFoundError(f"Base própria não encontrada em {caminho}.")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: uma leitura fixa atravessa as threads do pipeline, uma chamada por vez.
    conexao = sqlite3.connect(caminho, timeout=30, check_same_thread=False)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    versao = conexao.execute("PRAGMA user_version").fetchone()[0]
    if criar and versao == 0:
        conexao.executescript(ESQUEMA)
        # WAL: uma análise lê um estado fixo da base enquanto uma carga grava.
        conexao.execute("PRAGMA journal_mode = WAL")
        conexao.execute(f"PRAGMA user_version = {VERSAO_ESQUEMA}")
    elif versao != VERSAO_ESQUEMA:
        conexao.close()
        instrucao = "Rode python -m veritai.base migrar." if versao in MIGRACOES else "Recrie a base."
        raise RuntimeError(f"A base em {caminho} tem esquema {versao}; esta versão espera {VERSAO_ESQUEMA}. {instrucao}")
    return conexao


def migrar(caminho: str | Path) -> list[int]:
    """Leva a base à versão atual do esquema; devolve as versões de origem aplicadas."""
    caminho = Path(caminho)
    if not caminho.exists():
        raise FileNotFoundError(f"Base própria não encontrada em {caminho}.")
    aplicadas = []
    conexao = sqlite3.connect(caminho, timeout=30)
    try:
        versao = conexao.execute("PRAGMA user_version").fetchone()[0]
        if versao != VERSAO_ESQUEMA and versao not in MIGRACOES:
            raise RuntimeError(f"A base em {caminho} tem esquema {versao}, sem migração para {VERSAO_ESQUEMA}. Recrie a base.")
        while versao in MIGRACOES:
            # executescript confirma a transação aberta; cada passo vai numa transação própria.
            conexao.executescript(f"BEGIN; {MIGRACOES[versao]} PRAGMA user_version = {versao + 1}; COMMIT;")
            aplicadas.append(versao)
            versao += 1
    finally:
        conexao.close()
    return aplicadas


@contextmanager
def abrir(caminho: str | Path, criar: bool = False) -> Iterator[sqlite3.Connection]:
    """Conexão que confirma a transação ao sair sem erro, desfaz em caso de erro e sempre fecha."""
    conexao = conectar(caminho, criar)
    try:
        with conexao:
            yield conexao
    finally:
        conexao.close()


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def nova_carga(conexao: sqlite3.Connection, comando: str) -> str:
    identificador = f"base-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    conexao.execute("INSERT INTO cargas (id, criada_em, comando) VALUES (?, ?, ?)", (identificador, agora(), comando))
    return identificador


def snapshot_atual(conexao: sqlite3.Connection) -> str | None:
    linha = conexao.execute("SELECT id FROM cargas ORDER BY criada_em DESC, rowid DESC LIMIT 1").fetchone()
    return linha["id"] if linha else None
