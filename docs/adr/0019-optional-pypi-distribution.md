# ADR 0019: Keep PyPI publication optional

Date: 2026-09-12 · Status: Accepted

## Context

Micro-Agents is an open-source, self-hosted reference framework. A source
checkout, a GitHub Release wheel/sdist, or a GHCR image is sufficient to run
the project. The repository does not operate a managed production service.

The release workflow previously treated PyPI publication as a mandatory step,
which made the first GitHub/GHCR release depend on a PyPI-side trusted-publisher
configuration even when a maintainer did not want to publish a Python package
there.

## Decision

Keep GitHub Releases and GHCR as the default release distribution paths. Keep
the PyPI publisher action in a separate tag-triggered job guarded by the
`ENABLE_PYPI_PUBLISH` repository variable. The job requires the owner to
configure a matching PyPI trusted publisher before enabling the variable.

PyPI is therefore an optional distribution channel, not a runtime dependency,
release gate, or project-completion requirement.

## Consequences

- A release tag can produce and verify GitHub artifacts and a signed GHCR image
  without a PyPI account or publisher configuration.
- Maintainers can enable PyPI later without changing the release workflow's
  OIDC model or adding a long-lived upload token.
- Documentation must describe source, GitHub Release, and GHCR installation
  paths without assuming that `pip install micro-agents` is available.
