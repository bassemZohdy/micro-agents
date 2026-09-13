"""Release-alignment validation (P1.10): schema, image tag, changelog."""

from tools import validate_release


class TestReleaseValidation:
    def test_repo_state_is_release_aligned(self):
        # Non-release checks: schema version segment, immutable image pin.
        validate_release.validate()

    def test_changelog_version_detection(self):
        assert validate_release.changelog_mentions("0.1.0")
        assert not validate_release.changelog_mentions("9.9.9")

    def test_release_mode_requires_matching_tag_and_changelog(self, monkeypatch):
        monkeypatch.setattr(validate_release, "package_version", lambda: "2.0.0")
        monkeypatch.setattr(validate_release, "deployment_image", lambda: "ghcr.io/example:2.0.0")
        monkeypatch.setattr(validate_release, "changelog_mentions", lambda version: False)
        try:
            validate_release.validate(release=True, tag_version="2.0.0")
        except AssertionError as exc:
            assert "CHANGELOG" in str(exc)
        else:
            raise AssertionError("mismatched changelog must fail at release time")

    def test_release_workflow_signs_and_attests_the_published_image(self):
        workflow = (validate_release.ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        assert "sigstore/cosign-installer@v3" in workflow
        assert "cosign sign --yes" in workflow
        assert "cosign verify" in workflow
        assert "cosign attest --yes" in workflow
        assert "cosign verify-attestation" in workflow
        assert "steps.image-build.outputs.digest" in workflow

    def test_docker_hub_publication_is_default_and_creates_repository(self):
        workflow = (validate_release.ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        publish_job, dockerhub_job = workflow.split("  publish:\n", 1)[1].split(
            "  publish-dockerhub:\n", 1
        )

        assert "pypa/gh-action-pypi-publish" not in workflow
        assert "if: vars.ENABLE_DOCKERHUB_PUBLISH" not in dockerhub_job
        assert "name: Publish image to Docker Hub" in dockerhub_job
        assert "id: dockerhub_image" in dockerhub_job
        assert 'namespace="${DOCKERHUB_USERNAME,,}"' in dockerhub_job
        assert "DOCKERHUB_USERNAME" in dockerhub_job
        assert "DOCKERHUB_TOKEN" in dockerhub_job
        assert "https://hub.docker.com/v2/auth/token" in dockerhub_job
        assert "/v2/namespaces/${DOCKERHUB_NAMESPACE}/repositories/micro-agents" in dockerhub_job
        assert "'{name: $name, namespace: $namespace" in dockerhub_job
        assert "is_private: false" in dockerhub_job
        assert "steps.dockerhub_image.outputs.image" in dockerhub_job
        assert "docker/build-push-action@v6" in dockerhub_job

        assert "id: ghcr_image" in publish_job
        assert "ghcr.io/${GITHUB_REPOSITORY,,}" in publish_job
        assert "steps.ghcr_image.outputs.image" in publish_job
