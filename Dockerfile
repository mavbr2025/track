FROM mcr.microsoft.com/playwright/python:v1.61.0-noble@sha256:a9731514f24121d1dcd25d58d0a38146646d290a5998fd80d3e533e7b5e21c69

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml README.md requirements.lock build-requirements.lock /app/
COPY src /app/src
COPY scripts /app/scripts

RUN python3 -m pip install --require-hashes -r /app/requirements.lock -r /app/build-requirements.lock && \
    python3 -m pip install --no-deps --no-build-isolation /app && \
    useradd --create-home --shell /usr/sbin/nologin appuser && \
    chown -R appuser:appuser /app && \
    chmod +x /app/scripts/run_sync.sh /app/scripts/run_sync_loop.sh

EXPOSE 10000

USER appuser

CMD ["shipment-api"]
