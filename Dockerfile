# Serves the built site. Practice code is compiled and run in visitors' browsers (Clang/WASM,
# downloaded during the build), so the image has no compiler and runs no submitted code.
FROM python:3.13-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 10001 app
WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:$PATH

COPY pyproject.toml uv.lock README.md .python-version ./
COPY sitegen ./sitegen
COPY content ./content
COPY static ./static
COPY examples ./examples

RUN uv sync --frozen --no-dev \
 && uv run --no-sync cpp-guide build \
 && chown -R app:app /app

USER app
EXPOSE 8000
CMD ["cpp-guide", "serve", "--host", "0.0.0.0", "--port", "8000", "--no-watch", "--no-build"]
