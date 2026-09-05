FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching. asyncpg, aioboto3 and redis are
# base dependencies, so the image can reach any backend by configuration alone.
# The two extras add what only this image needs: arq for the worker process and
# psycopg2 for the Alembic CLI.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[worker,postgres]"

# Only the Alembic CLI needs this: the app migrates on startup through the
# connection it already holds. It is here so `alembic upgrade head` works as a
# separate deploy step inside the container.
COPY alembic.ini ./

# Drop root. The app writes to one directory, and only when object storage and
# the database are left on their local defaults.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/.eve_storage \
    && chown -R app:app /app
USER app

# The default matches Caddy's `reverse_proxy api:8000` and compose's `expose`.
# PORT overrides it, because Render, Fly, Cloud Run and Heroku all assign one
# and health-check that port rather than the one the image chose.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8000') + '/health', timeout=4).status == 200 else 1)"

# `exec` so uvicorn is PID 1 and receives SIGTERM: the lifespan's shutdown stops
# the reprobe runner, the DNSBL monitor and the session janitor, and a signal
# swallowed by the shell would skip all three.
CMD ["sh", "-c", "exec uvicorn eve.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
