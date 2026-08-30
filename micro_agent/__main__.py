"""Micro-Agent process entrypoint.

Usage:
    python -m micro_agent --definition path/to/agent.yaml [--port 8080]
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_file
from micro_agent.interoperability import create_app
from micro_agent.observability import HealthChecker
from runtimes.adk import AdkRuntime


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
    runtime = AdkRuntime()
    agent = DefaultMicroAgent(definition, runtime)

    await agent.initialize()
    await agent.start()

    health_checker = HealthChecker()
    health_checker.add_dependency("runtime")

    app = create_app(agent, health_checker)

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        await agent.stop()
        await agent.shutdown()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
