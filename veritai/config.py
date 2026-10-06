"""Configuração versionada da VeritAI e segredos lidos do ambiente."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv


PACKAGE_DIR = Path(__file__).resolve().parent
load_dotenv(PACKAGE_DIR.parent / ".env")

CONFIG = json.loads((PACKAGE_DIR / "config.json").read_text(encoding="utf-8"))
LIMIARES = CONFIG["limiares_triagem"]

GOOGLE_FACT_CHECK_API_KEY = os.getenv("GOOGLE_FACT_CHECK_API_KEY", "").strip()


def versoes() -> dict[str, str]:
    return {
        "modelo": CONFIG["versao_modelo"],
        "nli": CONFIG["modelos"]["nli"],
        "embedding": CONFIG["modelos"]["embedding"],
        "config": CONFIG["versao_config"],
        "base_evidencias": CONFIG["base_evidencias"],
    }
