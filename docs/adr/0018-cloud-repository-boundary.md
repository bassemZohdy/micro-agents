# ADR 0018: Defer the cloud repository split until contracts stabilize

## Context

The `cloud` package contains the C0–C5 reference control-plane slices, while
the standalone `micro_agent` package remains independently deployable and does
not import cloud code. The cloud workstream has separate service boundaries,
but its descriptor, configuration, gateway, and observability contracts are
still evolving alongside the standalone distribution and deployment boundary.

The backlog calls for evaluating whether `cloud` should move to its own
repository and deployment package. That evaluation must distinguish a
repository boundary from the already-established runtime and deployment
boundaries.

## Options considered

1. **Split immediately.** This would create independent repository ownership
   and release automation now, but would duplicate contract fixtures and CI
   while the reference APIs are still changing.
2. **Keep the current monorepo permanently.** This minimizes operations, but
   would remove the option to give the cloud services an independent lifecycle
   once they become a product.
3. **Defer the split with explicit boundaries.** Keep the code together while
   contracts stabilize, retain independent package/import and deployment
   boundaries, and split when measurable ownership and release criteria are
   met.

## Decision

Choose option 3. Do not create a second repository or publish the `cloud`
package as part of the standalone framework yet. Maintain the existing
boundaries:

- `micro_agent` never imports `cloud` and has no cloud-only runtime dependency;
- cloud services remain separate deployables that communicate over their
  declared HTTP/A2A contracts;
- cloud schema and compatibility fixtures remain checked in with the reference
  implementation until their versioning cadence is independently owned.

Revisit the split when all of the following are true: the cloud contracts have
a stable compatibility policy, cloud deployment ownership and incident
responsibility are assigned, independent versioning/release automation is
defined, and the duplicated contract/test boundary can be verified in CI.

## Consequences

- The current repository remains easy to validate as one reference contract
  testbed without implying that cloud is part of the published distribution.
- A future split remains straightforward because the import, deployment, and
  schema boundaries are explicit today.
- The split is intentionally a deferred architectural decision, not a claim
  that the cloud services will never need their own repository.
