FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CAIS_DATABASE_PATH=/app/data/cais.db \
    PORT=8000

WORKDIR /app

RUN groupadd --system cais && useradd --system --gid cais --home-dir /app cais

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY main.py motor_preditivo_seops.pkl features_modelo.pkl modelo_metricas.json ./

RUN mkdir -p /app/data && chown -R cais:cais /app
USER cais

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=4s --start-period=15s --retries=3 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)); raise SystemExit(0 if data['status'] in ('operational','degraded') and data['model']['loaded'] else 1)"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
