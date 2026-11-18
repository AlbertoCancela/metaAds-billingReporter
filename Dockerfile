FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Mexico_City

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El .env NO se copia a la imagen (ver .dockerignore): se monta al ejecutar
COPY meta_billing ./meta_billing
COPY config ./config
COPY tests ./tests

RUN useradd --create-home --uid 1000 app && mkdir -p /app/salidas /app/registros && chown app /app/salidas /app/registros
USER app

ENTRYPOINT ["python", "-m", "meta_billing"]
CMD ["--help"]
