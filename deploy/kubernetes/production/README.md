# Optional production manifests

Apply after the base manifests in `deploy/kubernetes/`:

```bash
kubectl apply -f production/
```

- `networkpolicy.yaml` — default-deny ingress/egress with HTTPS egress for
  model, MCP, and state providers; tighten the ingress namespace selector to
  your gateway.
- `poddisruptionbudget.yaml` — keeps at least one replica during voluntary
  disruptions.
- `hpa.yaml` — CPU-based autoscaling between 2 and 10 replicas; scale on
  request rate from the `/metrics` series for finer control.

## Topology spread

Add to the Deployment `spec.template.spec` (base image works with any
distribution of replicas; spread prevents co-location failures):

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: micro-agent
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app: micro-agent
```
