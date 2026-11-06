# The gate runs the project's checks on a snapshot of each milestone, without network, in this image.
# Dependencies are installed here into /opt/venv and linked into the snapshot as .venv (gate_links).
# Build: docker build -f python.Dockerfile -t my-python-app-gate:local /path/to/my-python-app
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
