FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
COPY src /app/src
COPY scripts /app/scripts

RUN pip install --upgrade pip && \
    pip install -e ".[browser]" && \
    chmod +x /app/scripts/run_sync.sh

EXPOSE 10000

CMD ["shipment-api"]
