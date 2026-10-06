"""Os testes nunca usam a base própria real: VERITAI_BASE aponta para um caminho que não existe."""

import os
import tempfile
from pathlib import Path

os.environ["VERITAI_BASE"] = str(Path(tempfile.gettempdir()) / "veritai-testes-sem-base" / "nao-existe.sqlite3")
