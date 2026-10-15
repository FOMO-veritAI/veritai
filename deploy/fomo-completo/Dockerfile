# syntax=docker/dockerfile:1
# FOMO completo: API do FOMO na porta 8000 e VeritAI só por dentro, em 127.0.0.1:8100.
# A API da VeritAI não tem autenticação, por isso nunca fica exposta. No Kubernetes local
# (deploy/k8s/local.yaml), a mesma imagem roda os dois serviços em Deployments separados. Da raiz:
#   docker build -f deploy/fomo-completo/Dockerfile -t ghcr.io/fomo-veritai/fomo-completo:main .
#   docker run --rm -p 127.0.0.1:8000:8000 -e TAKTA_PUBLISHER_KEY=... -e TAKTA_REVIEWER_KEY=... ghcr.io/fomo-veritai/fomo-completo:main

ARG PYTHON_IMAGE=python:3.12-slim

# --- Dependências da VeritAI e do backend do FOMO ----------------------------------------------
FROM ${PYTHON_IMAGE} AS dependencias
# Commit do repositório fomo com o backend usado na imagem (contrato copiado de veritai/relatorio.py).
ARG FOMO_COMMIT=fd51c491a3a8c876c6df505e66c671d65760ffb8
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv
RUN pip install "torch>=2.9,<3" --index-url https://download.pytorch.org/whl/cpu
WORKDIR /src
COPY pyproject.toml README.md ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['modelos']))" > requisitos.txt \
 && pip install -r requisitos.txt
ADD https://github.com/FOMO-veritAI/fomo/archive/${FOMO_COMMIT}.tar.gz /tmp/fomo.tar.gz
RUN mkdir -p /fomo \
 && tar -xzf /tmp/fomo.tar.gz -C /fomo --strip-components=1 "fomo-${FOMO_COMMIT}/backend" \
 && rm -rf /fomo/backend/tests /fomo/backend/.env* /tmp/fomo.tar.gz \
 && pip install -r /fomo/backend/requirements.txt \
 && python -c "import torch, sys; sys.exit('torch com CUDA na imagem' if torch.version.cuda else 0)" \
 && ! pip list 2>/dev/null | grep -i '^nvidia-'
COPY veritai ./veritai
RUN pip install --no-deps .

# --- Imagem final ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS final
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    HF_HOME=/opt/modelos \
    VERITAI_PERFIL=v0 \
    VERITAI_BASE=/app/backend/data/veritai.sqlite3 \
    VERITAI_MODO=servico \
    VERITAI_URL=http://127.0.0.1:8100 \
    TAKTA_DB_PATH=/app/backend/data/takta.sqlite3 \
    FOMO_CORS_ORIGINS=https://fomo-veritai.github.io \
    PORT=8000
COPY --from=dependencias /opt/venv /opt/venv
COPY --from=dependencias /fomo/backend /app/backend
# Modelos do perfil v0 dentro da imagem; depois disso, nada é baixado do Hugging Face.
RUN python -c "from huggingface_hub import snapshot_download; from veritai.config import perfil; p = perfil('v0'); [snapshot_download(m, allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt', 'sentencepiece*', '1_Pooling/*']) for m in (p['embedding'], p['nli'])]" \
 && chmod -R a+rX /opt/modelos
# Usuário sem privilégios (uid 1000). Só escreve em backend/data e /tmp.
RUN useradd --uid 1000 --create-home --home-dir /home/fomo --shell /usr/sbin/nologin fomo \
 && mkdir -p /app/backend/data \
 && chown -R 1000:1000 /app/backend/data
COPY --chmod=755 <<'EOF' /usr/local/bin/iniciar-fomo
#!/bin/bash
# VeritAI só em 127.0.0.1; se qualquer um dos dois processos parar, o container para e é reiniciado.
set -u
export HF_HUB_OFFLINE=1
uvicorn veritai.api:app --host 127.0.0.1 --port 8100 &
uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT}" &
wait -n
exit $?
EOF
USER 1000
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'], timeout=4).status == 200 else 1)"]
CMD ["iniciar-fomo"]
