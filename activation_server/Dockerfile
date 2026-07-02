FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.29" \
    "sqlalchemy>=2.0" "aiosqlite>=0.20" "pydantic>=2.6"

COPY activation_server ./activation_server

# the SQLite DB lives on a mounted volume
ENV ACTIVATION_DB=/data/activation.db
VOLUME ["/data"]

CMD ["uvicorn", "activation_server.main:app", "--host", "0.0.0.0", "--port", "8100"]
