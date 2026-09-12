FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies resolve from pyproject alone, so this layer only rebuilds when
# the dependency set changes — not on every source edit.
COPY pyproject.toml README.md ./
RUN mkdir -p app ingest && touch app/__init__.py ingest/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf /root/.cache

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY ingest ./ingest
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
