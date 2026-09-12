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

The base Deployment includes two topology-spread constraints: zone spreading
is best-effort and hostname spreading is required. The base Service includes
the conventional Prometheus scrape annotations for installations that enable
annotation-based discovery. The NetworkPolicy permits DNS and HTTPS egress as
a portable baseline; replace its empty `to` selectors with provider-specific
IP blocks or namespace selectors before production use.

## Topology spread

The base Deployment already applies the zone and hostname spread constraints
shown above. Keep the hostname rule as `DoNotSchedule` when two or more
replicas are required to avoid co-location failures.
