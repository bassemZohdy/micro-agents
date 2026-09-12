"""Deployment and supply-chain guardrails over the committed manifests.

Keeps the P1.9 guarantees from regressing: immutable image references, no
pinned runtime UIDs, and no Secret committed as an appliable manifest.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEPLOY_DIR = Path(__file__).parent.parent / "deploy" / "kubernetes"


def _manifests() -> dict[Path, dict]:
    result = {}
    for path in sorted(DEPLOY_DIR.rglob("*.yaml")):
        result[path] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return result


class TestDeploymentHardening:
    def test_image_reference_is_immutable(self):
        import yaml as _yaml

        deployment = _yaml.safe_load((DEPLOY_DIR / "deployment.yaml").read_text(encoding="utf-8"))
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        image = container["image"]
        assert not image.endswith(":latest")
        tag = image.rsplit(":", 1)[-1] if ":" in image.rsplit("@", 1)[-1] else ""
        assert tag and tag != "latest", f"image must use an immutable reference: {image}"

    def test_runtime_uid_is_not_pinned(self):
        deployment = yaml.safe_load((DEPLOY_DIR / "deployment.yaml").read_text(encoding="utf-8"))
        pod_spec = deployment["spec"]["template"]["spec"]
        security_context = pod_spec["securityContext"]
        assert security_context.get("runAsNonRoot") is True
        assert "runAsUser" not in security_context, "OpenShift assigns arbitrary UIDs"
        assert "runAsGroup" not in security_context

    def test_no_appliable_secret_is_committed(self):
        for path, manifest in _manifests().items():
            if manifest and manifest.get("kind") == "Secret":
                assert path.name.endswith(".template.yaml"), (
                    f"{path.name} is an appliable Secret; keep secrets as *.template.yaml"
                )
        assert (DEPLOY_DIR / "secret.template.yaml").exists()

    def test_production_examples_are_valid(self):
        manifests = {p.name: m for p, m in _manifests().items() if "production" in str(p)}
        kinds = {m["kind"] for m in manifests.values()}
        assert {"NetworkPolicy", "PodDisruptionBudget", "HorizontalPodAutoscaler"} <= kinds

    def test_deployment_spreads_replicas_and_leaves_shutdown_cushion(self):
        deployment = yaml.safe_load((DEPLOY_DIR / "deployment.yaml").read_text(encoding="utf-8"))
        pod_spec = deployment["spec"]["template"]["spec"]
        constraints = pod_spec["topologySpreadConstraints"]
        assert {constraint["topologyKey"] for constraint in constraints} == {
            "topology.kubernetes.io/zone",
            "kubernetes.io/hostname",
        }
        assert pod_spec["terminationGracePeriodSeconds"] == 30

        definition_config = yaml.safe_load(
            (DEPLOY_DIR / "definition-configmap.yaml").read_text(encoding="utf-8")
        )
        definition = yaml.safe_load(definition_config["data"]["agent.yaml"])
        assert definition["spec"]["runtime"]["shutdown_timeout_seconds"] == 25

    def test_service_advertises_prometheus_scrape_contract(self):
        service = yaml.safe_load((DEPLOY_DIR / "service.yaml").read_text(encoding="utf-8"))
        assert service["metadata"]["annotations"] == {
            "prometheus.io/scrape": "true",
            "prometheus.io/path": "/metrics",
            "prometheus.io/port": "8080",
        }

    def test_image_and_manifest_support_restricted_runtime_filesystem(self):
        dockerfile = (DEPLOY_DIR.parent.parent / "Dockerfile").read_text(encoding="utf-8")
        assert "chmod -R g=u /app /home/appuser" in dockerfile

        deployment = yaml.safe_load((DEPLOY_DIR / "deployment.yaml").read_text(encoding="utf-8"))
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert any(
            volume["name"] == "tmp" and volume["emptyDir"] == {}
            for volume in deployment["spec"]["template"]["spec"]["volumes"]
        )
