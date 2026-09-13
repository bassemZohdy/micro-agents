# ADR 0019: Use container images as the primary distribution

Date: 2026-09-13 · Status: Accepted

## Context

Micro-Agents is an open-source, self-hosted reference framework whose primary
runtime is a network service. A source checkout or a container image is enough
to run it, and the Dockerfile already installs the local package and its
runtime lock. The project does not operate a managed production service.

The repository previously made PyPI publication part of the release workflow,
although a Python package index is not needed for container-based deployment.
The existing GHCR flow already builds, signs, attests, and publishes the image.

## Decision

Use the signed GHCR image as the canonical container distribution and attach
wheel/sdist artifacts to GitHub Releases for source-oriented users. Do not
require PyPI publication.

Publish a public Docker Hub mirror through the `publish-dockerhub` release job
on every release tag. The job uses the Docker Hub namespace in
`DOCKERHUB_USERNAME` and the `DOCKERHUB_TOKEN` repository secret, creates the
public `micro-agents` repository through the namespace-scoped Hub API when it
does not exist, rebuilds the tagged image, and signs/verifies its digest with
the release workflow identity.

## Consequences

- Self-hosters can use GHCR, Docker Hub, a GitHub Release artifact, or a source
  checkout without depending on PyPI.
- GHCR remains the provenance-backed canonical registry; Docker Hub is the
  discoverable public mirror whose namespace and token are owner configuration.
- Publishing to Docker Hub requires a scoped read/write token stored only in
  GitHub Actions secrets; the release job creates the public repository when
  needed and refuses to change an existing private repository.
- The Python package metadata remains useful for local development and release
  artifacts without promising that `pip install micro-agents` is available.
