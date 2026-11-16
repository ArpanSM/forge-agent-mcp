FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONSOLE_HOST=0.0.0.0 \
    GATEWAY_HOST=127.0.0.1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY corpus ./corpus
RUN python -m pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 forge \
    && mkdir -p /app/data \
    && chown -R forge:forge /app
USER forge

EXPOSE 8000

# Local demo only: gateway, agent and reviewer console in one process.
CMD ["python", "-m", "forge_agent_mcp.console", "demo", "--auto", "--serve", "--keep"]
