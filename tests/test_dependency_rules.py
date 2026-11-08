"""Enforce the package layering and optional-dependency rules.

Rules (from the project definition and the distribution plan):

* ``micro_agent`` never imports the ``cloud`` reference code.
* ``runtimes`` never import ``cloud``, and only the ``micro_agent.config``
  composition root imports ``runtimes``.
* A module's *import-time* graph between subpackages is acyclic. Imports under
  ``TYPE_CHECKING`` and inside functions are lazy and therefore excluded.
* Libraries outside the base install (Redis, asyncpg, MCP, A2A, PyJWT, boto3,
  Google ADK, OpenTelemetry, ...) are never imported unguarded at module level,
  so ``import micro_agent`` works on a base install.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROJECT_PACKAGES = ("micro_agent", "runtimes", "cloud")

# Runtime dependencies from ``pyproject.toml`` plus their transitive helpers.
BASE_INSTALL = {
    "pydantic",
    "yaml",
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
    "anyio",
    "h11",
    "click",
    "typing_extensions",
    "annotated_types",
    "idna",
    "sniffio",
    "certifi",
}


def import_time_imports(source: str) -> list[tuple[str, int, bool]]:
    """Return ``(module, line, guarded)`` for imports that run on ``import``.

    ``TYPE_CHECKING`` blocks are skipped. ``try`` bodies and their handlers are
    marked ``guarded`` because an ``ImportError`` fallback is the idiom for an
    optional dependency. Function and class bodies are never entered.
    """
    found: list[tuple[str, int, bool]] = []

    def visit(body: list[ast.stmt], guarded: bool) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                found.extend((alias.name, node.lineno, guarded) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    found.append((node.module, node.lineno, guarded))
            elif isinstance(node, ast.If):
                if "TYPE_CHECKING" in ast.unparse(node.test):
                    continue
                visit(node.body, guarded)
                visit(node.orelse, guarded)
            elif isinstance(node, ast.Try):
                visit(node.body, True)
                for handler in node.handlers:
                    visit(handler.body, guarded)
                visit(node.orelse, guarded)
                visit(node.finalbody, guarded)

    visit(ast.parse(source).body, False)
    return found


def find_cycles(edges: dict[str, set[str]]) -> set[tuple[str, ...]]:
    cycles: set[tuple[str, ...]] = set()

    def dfs(node: str, path: list[str]) -> None:
        for target in edges.get(node, ()):
            if target in path:
                cycles.add(tuple(sorted(path[path.index(target) :])))
            elif target in edges:
                dfs(target, [*path, target])

    for start in list(edges):
        dfs(start, [start])
    return cycles


def _modules() -> list[tuple[str, str, Path]]:
    """``(package, dotted module, path)`` for every project source file."""
    modules = []
    for package in PROJECT_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            dotted = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            modules.append((package, dotted.removesuffix(".__init__"), path))
    return modules


def _subpackage(dotted: str) -> str:
    parts = dotted.split(".")
    return ".".join(parts[:2])


# ---------------------------------------------------------------------------
# The analyzer itself must be able to fail
# ---------------------------------------------------------------------------


def test_analyzer_skips_type_checking_function_level_and_flags_the_rest():
    source = """
import json
import redis
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import micro_agent.runtime

try:
    import asyncpg
except ImportError:
    asyncpg = None

def lazy():
    import boto3
"""
    found = {(module, guarded) for module, _line, guarded in import_time_imports(source)}
    assert ("redis", False) in found  # unguarded optional dependency is visible
    assert ("asyncpg", True) in found
    assert not any(module in {"micro_agent.runtime", "boto3"} for module, _ in found)


def test_cycle_detector_finds_two_and_three_node_cycles():
    two = find_cycles({"a": {"b"}, "b": {"a"}})
    assert two == {("a", "b")}
    three = find_cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}})
    assert three == {("a", "b", "c")}
    assert find_cycles({"a": {"b"}, "b": {"c"}, "c": set()}) == set()


# ---------------------------------------------------------------------------
# The repository must obey the rules
# ---------------------------------------------------------------------------


def test_layering_between_core_runtimes_and_cloud():
    violations = []
    for package, dotted, path in _modules():
        for module, line, _guarded in import_time_imports(path.read_text(encoding="utf-8")):
            top = module.split(".")[0]
            if package == "micro_agent" and top == "cloud":
                violations.append(f"{dotted}:{line} imports cloud")
            if package == "runtimes" and top == "cloud":
                violations.append(f"{dotted}:{line} imports cloud")
            if (
                package == "micro_agent"
                and top == "runtimes"
                and not dotted.startswith("micro_agent.config")
            ):
                violations.append(f"{dotted}:{line} imports a runtime outside the composition root")
    assert violations == []


def test_no_import_time_cycles_between_subpackages():
    edges: dict[str, set[str]] = defaultdict(set)
    for _package, dotted, path in _modules():
        source = _subpackage(dotted)
        for module, _line, _guarded in import_time_imports(path.read_text(encoding="utf-8")):
            if module.split(".")[0] not in PROJECT_PACKAGES or "." not in module:
                continue
            target = _subpackage(module)
            if target != source:
                edges[source].add(target)
    assert find_cycles(edges) == set()


def test_optional_third_party_libraries_are_never_imported_unguarded():
    stdlib = set(sys.stdlib_module_names)
    violations = []
    for _package, dotted, path in _modules():
        for module, line, guarded in import_time_imports(path.read_text(encoding="utf-8")):
            top = module.split(".")[0]
            if top in PROJECT_PACKAGES or top in stdlib or top in BASE_INSTALL or guarded:
                continue
            violations.append(f"{dotted}:{line} imports {module}")
    assert violations == []


@pytest.mark.parametrize(
    "package", ["micro_agent.session", "micro_agent.security", "micro_agent.mcp"]
)
def test_optional_backends_import_without_their_libraries(package):
    """Blocking every optional library must not break importing the package."""
    import subprocess

    blocked = ["redis", "asyncpg", "mcp", "a2a", "jwt", "boto3", "opentelemetry", "google"]
    code = (
        "import sys\n"
        f"for name in {blocked!r}:\n"
        "    sys.modules[name] = None\n"  # None makes `import name` raise ImportError
        f"import {package}\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# Runtime adapters are composition layers; cap their fan-out so it cannot grow
# unnoticed. Raising a limit is a deliberate, reviewed decision.
ADAPTER_SUBPACKAGE_LIMITS = {"runtimes.adk": 14, "runtimes.google_adk": 13}


@pytest.mark.parametrize(("adapter", "limit"), sorted(ADAPTER_SUBPACKAGE_LIMITS.items()))
def test_runtime_adapter_coupling_does_not_grow(adapter, limit):
    targets: set[str] = set()
    for package, dotted, path in _modules():
        if package != "runtimes" or _subpackage(dotted) != adapter:
            continue
        for module, _line, _guarded in import_time_imports(path.read_text(encoding="utf-8")):
            if module.split(".")[0] == "micro_agent" and "." in module:
                targets.add(_subpackage(module))
    assert len(targets) <= limit, sorted(targets)
