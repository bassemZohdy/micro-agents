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
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check .

# Run type checking
mypy micro_agent
```

## Pull Request Guidelines

- Keep PRs focused on a single change.
- Include tests for new functionality.
- Update documentation when behavior changes.
- Ensure all CI checks pass before requesting review.

## Commit Messages

Use clear, descriptive commit messages:

```
type(scope): short description

Longer explanation if necessary.
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.

## Architecture Decisions

Significant architectural changes should include an ADR (Architecture Decision Record) in `docs/adr/`.

## Code of Conduct

Be respectful and constructive in all interactions.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
