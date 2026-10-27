FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOGSTORE_ENV=production \
    LOGSTORE_HOST=0.0.0.0 \
    LOGSTORE_PORT=5000 \
    LOGSTORE_PATH=/app/data/webdb \
    LOGSTORE_BACKUP_PATH=/app/data/backups \
    LOGSTORE_STATE_PATH=/app/data/.logstore-state.json

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --system logstore \
    && useradd --system --gid logstore --no-create-home --shell /usr/sbin/nologin logstore \
    && mkdir -p /app/data \
    && chown logstore:logstore /app/data

COPY --chown=logstore:logstore app.py ./app.py
COPY --chown=logstore:logstore logstore ./logstore
COPY --chown=logstore:logstore webapp ./webapp
COPY --chown=logstore:logstore templates ./templates

USER logstore:logstore
EXPOSE 5000
CMD ["python", "app.py"]
