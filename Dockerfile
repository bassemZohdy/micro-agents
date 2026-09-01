FROM python:3.11-slim AS base

# OpenShift and Pod Security policies run containers with an arbitrary UID in
# group 0. Application files are root-owned and group-accessible so any
# runtime UID works; the declared user is only the non-root default for
# plain Docker and Kubernetes.
RUN useradd --uid 1001 --gid 0 --system --create-home appuser

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY micro_agent/ micro_agent/
COPY runtimes/ runtimes/

RUN pip install --no-cache-dir . &&     chmod -R g=u /app /home/appuser

USER 1001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live')" || exit 1

ENTRYPOINT ["python", "-m", "micro_agent"]
CMD ["--definition", "/etc/micro-agent/agent.yaml"]
