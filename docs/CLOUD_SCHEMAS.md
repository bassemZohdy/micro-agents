# Cloud Contract Schemas (C5)

The cloud reference planes use a separate, versioned contract track from the
core `MicroAgentDefinition`. The checked-in JSON Schema artifacts are generated
from the Pydantic boundary models in `cloud.schemas`:

| Contract | Artifact | Boundary |
| --- | --- | --- |
| Agent descriptor | `cloud-descriptor-v1alpha1.json` | registry registration and discovery |
| Config record | `cloud-config-v1alpha1.json` | versioned config responses |
| Observability batch | `cloud-observability-v1alpha1.json` | event ingestion |

Each artifact has a stable `$id` under
`https://microagents.io/schemas/cloud/v1alpha1/`. Regenerate all three after a
contract model change:

```bash
python -c "from cloud.schemas import write_schemas; write_schemas()"
```

## Compatibility policy

- `v1alpha1` is the current cloud contract version. A producer must emit the
  required identity and envelope fields defined by its artifact.
- Descriptor, config-record, and batch envelope objects reject unknown fields
  so a typo cannot silently change control-plane meaning. Observability event
  objects allow additive fields because producers may attach new telemetry
  dimensions; the stable fields used for aggregation remain unchanged.
- Definition records retain the core `microagents.io/v1alpha1` or
  `microagents.io/v1beta1` document in `payload`; the core versioned loader is
  authoritative for that inner document. Overlay records retain the strict
  `EnvironmentOverlay` contract.
- Additive event fields and new optional fields are compatible within
  `v1alpha1`. Renamed or removed fields, changed meanings, new required
  fields, or a change to stored payload semantics require a new cloud schema
  version and an explicit migration boundary. Stored config versions are
  append-only; migration never rewrites an existing record.
- The reference apps validate descriptor, response-envelope, and observability
  boundaries with the same models used to generate these artifacts. Shared
  multi-replica storage and production rollout compatibility remain deployment
  work, as described in [Deployment](DEPLOYMENT.md).

The schemas formalize the reference-plane wire contracts; they do not turn the
SQLite implementations into production shared services.
