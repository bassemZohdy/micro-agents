"""Micro-Agent process entrypoint.

Usage:
    python -m micro_agent --definition path/to/agent.yaml [--port 8080]
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from micro_agent.config import build_authenticator, build_runtime
from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_file
from micro_agent.interoperability import create_app
from micro_agent.observability import HealthChecker, Telemetry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Micro-Agent runtime")
    parser.add_argument(
        "--definition",
        type=Path,
        required=True,
        help="Path to Micro-Agent YAML definition",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP port (default: 8080)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    definition = load_definition_from_file(args.definition)
    telemetry = Telemetry()
    bootstrap = build_runtime(definition, telemetry=telemetry)
    runtime = bootstrap.runtime
    agent = DefaultMicroAgent(definition, runtime)

    await agent.initialize()
    await agent.start()

    health_checker = HealthChecker()
    for name, probe in runtime.health_probes().items():
        health_checker.add_dependency(name, probe=probe)

    app = create_app(
        agent,
        health_checker,
        telemetry=telemetry,
        authenticator=build_authenticator(bootstrap.resolved),
    )

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level=bootstrap.resolved.log_level.lower(),
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        await agent.stop()
        await agent.shutdown()
        await runtime.close()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
