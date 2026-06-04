# FinOptic — container image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e .

# App assets (sample data, dashboard already under src).
COPY sample_data ./sample_data

# Run as a non-root user.
RUN useradd --create-home --uid 10001 finoptic && chown -R finoptic /app
USER finoptic

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "finoptic.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
