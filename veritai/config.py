"""Configuração versionada da VeritAI e segredos lidos do ambiente."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv


PACKAGE_DIR = Path(__file__).resolve().parent
load_dotenv(PACKAGE_DIR.parent / ".env")

CONFIG = json.loads((PACKAGE_DIR / "config.json").read_text(encoding="utf-8"))
LIMIARES = CONFIG["limiares_triagem"]
SELECAO = CONFIG["selecao"]
COPIAS = CONFIG["copias"]
BASE = CONFIG["base"]

GOOGLE_FACT_CHECK_API_KEY = os.getenv("GOOGLE_FACT_CHECK_API_KEY", "").strip()


def nome_perfil(nome: str | None = None) -> str:
    """Perfil pedido, ou o de VERITAI_PERFIL, ou o padrão do config."""
    nome = nome or os.getenv("VERITAI_PERFIL", "").strip() or CONFIG["perfil_padrao"]
    if nome not in CONFIG["perfis"]:
        raise ValueError(f"Perfil de modelo desconhecido: {nome!r}. Opções: {', '.join(CONFIG['perfis'])}.")
    return nome


def perfil(nome: str | None = None) -> dict[str, str]:
    return CONFIG["perfis"][nome_perfil(nome)]


def caminho_base() -> Path:
    return Path(os.getenv("VERITAI_BASE", "").strip() or PACKAGE_DIR.parent / "data" / "veritai.sqlite3")


def versoes(nome: str | None = None, snapshot_base: str | None = None) -> dict[str, str]:
    modelos = perfil(nome)
    return {
        "modelo": modelos["versao_modelo"],
        "nli": modelos["nli"],
        "embedding": modelos["embedding"],
        "config": CONFIG["versao_config"],
        # Com base própria, registra o snapshot da última carga; sem ela, só a busca na web.
        "base_evidencias": snapshot_base or CONFIG["base_evidencias"],
    }
