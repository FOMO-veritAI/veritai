# syntax=docker/dockerfile:1
# Imagem da VeritAI: API na porta 8100, sem modelos nem base dentro da imagem (por padrão).
#   docker build -t veritai:local .
#   docker build --target teste .                      # roda a suíte com Python 3.12 (não vira imagem final)
#   docker build --build-arg PERFIL_EMBUTIDO=v1 -t veritai:v1-embutido .

ARG PYTHON_IMAGE=python:3.12-slim

# --- Dependências em um venv isolado ---------------------------------------------------------
FROM ${PYTHON_IMAGE} AS dependencias
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv
# torch só-CPU primeiro: o pip não reinstala o que já satisfaz a versão, e a imagem não leva CUDA.
RUN pip install "torch>=2.9,<3" --index-url https://download.pytorch.org/whl/cpu
WORKDIR /src
COPY pyproject.toml ./
# Instala as dependências declaradas antes de copiar o código, para o cache do build sobreviver a mudanças no código.
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['modelos']))" > requisitos.txt \
 && pip install -r requisitos.txt \
 && python -c "import torch, sys; sys.exit('torch com CUDA na imagem' if torch.version.cuda else 0)" \
 && ! pip list 2>/dev/null | grep -i '^nvidia-'
COPY README.md ./
COPY veritai ./veritai
RUN pip install --no-deps .

# --- Testes com Python 3.12 (só com --target teste; não entra na imagem final) --------------------
FROM dependencias AS teste
RUN pip install "pytest>=8,<10"
COPY tests ./tests
COPY eval ./eval
RUN python -m pytest -q -p no:cacheprovider

# --- Imagem final ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS final
ARG PERFIL_EMBUTIDO=""
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/cache \
    VERITAI_BASE=/data/veritai.sqlite3 \
    HOME=/tmp
# uid/gid fixos (10001) para o securityContext do Kubernetes. O usuário só escreve em /cache, /data e /tmp;
# o diretório de trabalho é vazio e do root, para que nada relativo a ele seja criado ou carregado.
RUN groupadd --system --gid 10001 veritai \
 && useradd --system --no-create-home --uid 10001 --gid 10001 --home-dir /tmp --shell /usr/sbin/nologin veritai \
 && mkdir -p /cache /data /app \
 && chown veritai:veritai /cache /data
COPY --from=dependencias /opt/venv /opt/venv
USER veritai
WORKDIR /app
# Opcional: embute os modelos de um perfil em /cache (útil no Kubernetes, sem volume de cache).
RUN if [ -n "$PERFIL_EMBUTIDO" ]; then \
      python -c "import sys; from huggingface_hub import snapshot_download; from veritai.config import perfil; p = perfil(sys.argv[1]); [snapshot_download(m, allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt', 'sentencepiece*', '1_Pooling/*']) for m in (p['embedding'], p['nli'])]" "$PERFIL_EMBUTIDO"; \
    fi
EXPOSE 8100
VOLUME ["/cache", "/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "veritai.api:app", "--host", "0.0.0.0", "--port", "8100"]
