# Contributing to Micro-Agents

Thank you for your interest in contributing to Micro-Agents.

## Getting Started

1. Fork the repository.
2. Clone your fork locally.
3. Create a feature branch from `main`.
4. Make your changes.
5. Run tests and ensure CI passes.
6. Submit a pull request.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff check .
ruff format --check .
mypy micro_agent runtimes
pytest -q

# Definition/schema drift
python -m micro_agent.definition.schema
git diff --exit-code docs/schemas/micro-agent-v1alpha1.json

# Documentation
pip install mkdocs mkdocs-material
mkdocs build --strict
```

See [Implementation Status](docs/IMPLEMENTATION_STATUS.md) before starting.
It records known failures on the latest audited commit; contributors should
not hide or waive them locally.

Use the existing pytest markers for targeted runs:

```bash
pytest -m "not integration and not e2e"
pytest -m integration
pytest -m e2e
```

## Pull Request Guidelines

- Keep PRs focused on a single change.
- Include tests for new functionality.
- Update documentation when behavior changes.
- Update `TODO.md` only for open work and `CHANGELOG.md` for completed work.
- Ensure all CI checks pass before requesting review.

## Commit Messages

Use clear, descriptive commit messages:

```
type(scope): short description

Longer explanation if necessary.
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.

## Architecture Decisions

Significant architectural changes should include an ADR (Architecture Decision
Record) in `docs/adr/`. An ADR must distinguish an accepted design from its
implementation status; accepting a decision does not prove conformance.

## Code of Conduct

Be respectful and constructive in all interactions.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
