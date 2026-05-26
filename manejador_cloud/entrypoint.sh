#!/bin/bash
set -e

echo "Waiting for PostgreSQL (primary)..."
until python -c "
import os, psycopg2
try:
    psycopg2.connect(
        dbname=os.environ.get('DATABASE_NAME','cloud_db'),
        user=os.environ.get('DATABASE_USER','admin'),
        password=os.environ.get('DATABASE_PASSWORD','admin123'),
        host=os.environ.get('DATABASE_HOST','localhost'),
        port=os.environ.get('DATABASE_PORT','5432')
    )
    print('PostgreSQL ready')
except Exception as e:
    print(f'PostgreSQL not ready: {e}')
    exit(1)
"; do
    sleep 2
done

echo "Seeding cloud data (idempotent)..."
python seed.py

echo "Starting uvicorn on port ${PORT:-8002}..."
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8002}" \
    --workers 4 \
    --log-level "${LOG_LEVEL:-info}"
