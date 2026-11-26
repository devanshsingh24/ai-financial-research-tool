# syntax=docker/dockerfile:1.7
# FinSight API image.
#   builder : venv with CPU-only torch + pinned requirements; bge-small model downloaded
#   runtime : slim, non-root, no build tools, no secrets, model baked in (offline at runtime)
#   test    : runtime + pytest + tests/ (docker compose --profile test run --rm tests)

ARG PYTHON_IMAGE=python:3.11-slim
ARG TORCH_VERSION=2.15.0
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5

# ---------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder
ARG TORCH_VERSION
ARG EMBED_MODEL
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH HF_HOME=/opt/hf
RUN python -m venv /opt/venv
# CPU-only torch first, so sentence-transformers doesn't pull the multi-GB CUDA build.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}"
COPY backend/requirements.txt backend/constraints.txt /tmp/req/
RUN pip install -r /tmp/req/requirements.txt -c /tmp/req/constraints.txt
# Bake the embedding model into the image: no download (or Hugging Face access) at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('${EMBED_MODEL}')" \
 && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ARG EMBED_MODEL
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH \
    HF_HOME=/opt/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    HF_EMBED_MODEL=${EMBED_MODEL} \
    FINSIGHT_DATA_DIR=/data INDEX_PATH=/data/faiss_index \
    USER_AGENT=finsight-api \
    WEB_CONCURRENCY=1 PORT=8000
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data/faiss_index /app \
 && chown -R app:app /data
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf /opt/hf
WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app backend ./backend
COPY --chown=app:app docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh
USER app
EXPOSE 8000
VOLUME ["/data"]
# /health checks the app and its database (503 when the DB is unreachable).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4).status == 200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["api"]

# ---------------------------------------------------------------- test
FROM runtime AS test
USER root
RUN pip install --no-cache-dir pytest httpx
COPY --chown=app:app pytest.ini ./
COPY --chown=app:app tests ./tests
USER app
CMD ["pytest", "-q"]
