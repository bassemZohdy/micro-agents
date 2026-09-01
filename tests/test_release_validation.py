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
