FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY querypilot ./querypilot
RUN pip install ".[dev]"
COPY . .

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "querypilot.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
