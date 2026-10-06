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

GOOGLE_FACT_CHECK_API_KEY = os.getenv("GOOGLE_FACT_CHECK_API_KEY", "").strip()


def nome_perfil(nome: str | None = None) -> str:
    """Perfil pedido, ou o de VERITAI_PERFIL, ou o padrão do config."""
    nome = nome or os.getenv("VERITAI_PERFIL", "").strip() or CONFIG["perfil_padrao"]
    if nome not in CONFIG["perfis"]:
        raise ValueError(f"Perfil de modelo desconhecido: {nome!r}. Opções: {', '.join(CONFIG['perfis'])}.")
    return nome


def perfil(nome: str | None = None) -> dict[str, str]:
    return CONFIG["perfis"][nome_perfil(nome)]


def versoes(nome: str | None = None) -> dict[str, str]:
    modelos = perfil(nome)
    return {
        "modelo": modelos["versao_modelo"],
        "nli": modelos["nli"],
        "embedding": modelos["embedding"],
        "config": CONFIG["versao_config"],
        "base_evidencias": CONFIG["base_evidencias"],
    }
