FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.4

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data ./data
COPY web ./web
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}" \
    PORT=8000

EXPOSE 8000
CMD ["sh", "-c", "uvicorn aethersparse.service:app --host 0.0.0.0 --port ${PORT}"]
