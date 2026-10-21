FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FACTORY_STATE_ROOT=/mnt/workspace \
    DEEPAGENTS_CODE_READ_PROJECT_DOTENV=false
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[agent,client]' && dcode --version
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /mnt/workspace && chown agent:agent /mnt/workspace
USER agent
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
# One ASGI worker per isolated runtime session; never use --workers > 1.
CMD ["uvicorn", "agent_factory.app:app", "--host", "0.0.0.0", "--port", "8080", "--ws-max-size", "61440", "--no-access-log"]
