FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Dependencias antes del código (mejor cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Usuario no-root. La app escribe en /app: uploads/, datasets/ (volúmenes) y
# los .xlsx legacy de la raíz, así que el directorio debe pertenecerle.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p uploads datasets \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5)"

# 1 worker + threads: el procesamiento Excel corre en hilos dentro de la app
# (estado global de cancelación/processing_status compartido entre handlers
# y worker thread; usar más de 1 worker rompería esa coordinación).
# Las escrituras Excel concurrentes están protegidas por utils/excel_lock
# (lock de archivo del SO), que también funcionaría con varios workers.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "-k", "gthread", "--timeout", "120", "app:app"]
