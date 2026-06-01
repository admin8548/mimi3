FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    MIMO_METRICS_DB_PATH=/app/data/gateway_metrics.db \
    MIMO_METRICS_SNAPSHOT_PATH=/app/data/gateway_snapshot.json \
    MIMO_PROCESS_LOCK_PATH=/app/data/mimo2api.lock

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py docker-entrypoint.sh ./
COPY model_mapping.json ./model_mapping.default.json
COPY mimo2api ./mimo2api
COPY users/.gitkeep ./users/.gitkeep

RUN mkdir -p /app/logs /app/data \
    && ln -sf /app/data/model_mapping.json /app/model_mapping.json \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R app:app /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.getenv('SERVER_PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/auth/session', timeout=3).read()"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "main.py"]
