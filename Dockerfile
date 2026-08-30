FROM python:3.11-slim AS base

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY micro_agent/ micro_agent/
COPY runtimes/ runtimes/

RUN pip install --no-cache-dir .

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live')" || exit 1

ENTRYPOINT ["python", "-m", "micro_agent"]
CMD ["--definition", "/etc/micro-agent/agent.yaml"]
