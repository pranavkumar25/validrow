FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching. The production backends are
# extras, and the image ships all of them: which one is used is decided by
# environment at runtime, so the image must be able to reach any of them.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[postgres,s3,redis,worker]"

EXPOSE 8000
CMD ["uvicorn", "eve.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
