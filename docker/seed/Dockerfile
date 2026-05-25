FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

COPY schema.sql /app/schema.sql
COPY seed.py /app/seed.py

ENV CH_HOST=clickhouse \
    CH_PORT=8123 \
    CH_USER=demo \
    CH_PASS=demo \
    CH_DATABASE=default \
    SEED_PROFILE=full \
    SEED_RESET=1

CMD ["python", "seed.py"]
