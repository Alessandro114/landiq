# LandIQ Engine — Python 3.12 Docker image
# Feasibility analysis + PDF generation

FROM python:3.12-slim AS production
WORKDIR /app

# System deps for WeasyPrint + PDF generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libffi-dev libcairo2 libglib2.0-0 \
    fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/
COPY scrapers/ ./scrapers/
COPY connectors/ ./connectors/
COPY data/ ./data/

# Reports output dir
RUN mkdir -p reports

# Non-root user
RUN useradd -r -s /bin/false landiq && chown -R landiq:landiq /app
USER landiq

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default: run as API server (FastAPI)
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
