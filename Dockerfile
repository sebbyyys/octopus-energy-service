# Dependencies are resolved by the checked-in uv.lock, never at image runtime.
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH" \
    OCTOPUS_DB_PATH=/app/data/octopus.sqlite3
WORKDIR /app
RUN groupadd --gid 10001 octopus && useradd --uid 10001 --gid 10001 \
    --no-create-home --shell /usr/sbin/nologin octopus \
    && mkdir /app/data && chown 10001:10001 /app/data
COPY --from=builder /app/.venv /app/.venv
USER 10001:10001
VOLUME ["/app/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=4).close()"]
# One process: the in-process sync scheduler must not run in duplicate workers.
CMD ["uvicorn", "octopus_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
