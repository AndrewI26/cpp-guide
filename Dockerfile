# One image, two roles (see docker-compose.yml):
#   web    - serves the built site and forwards /api/run to the runner
#   runner - compiles and runs practice submissions in an isolated container
FROM python:3.13-slim-trixie

# g++ (with libasan/libubsan for the sanitizers) is only needed by the runner, but keeping one
# image keeps the build simple.
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ \
 && rm -rf /var/lib/apt/lists/*

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
COPY starter ./starter
COPY examples ./examples

RUN uv sync --frozen --no-dev \
 && uv run --no-sync cpp-guide build \
 && chown -R app:app /app

USER app
EXPOSE 8000 9000
CMD ["cpp-guide", "serve", "--host", "0.0.0.0", "--port", "8000", "--no-watch", "--no-build"]
