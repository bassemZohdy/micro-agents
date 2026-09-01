"""Release alignment validation (P1.10).

Checks that stay true at any point in time, and stronger checks the release
workflow runs at tag time:

- the JSON Schema `$id` matches the definition API version,
- the committed Kubernetes deployment pins the package version,
- at tag time: the tag equals the package version, and CHANGELOG.md
  references the released version (maintainers move `[Unreleased]` into a
  versioned section before tagging).
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def package_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def schema_version() -> str:
    schema = json.loads(
        (ROOT / "docs" / "schemas" / "micro-agent-v1alpha1.json").read_text(encoding="utf-8")
    )
    schema_id = str(schema.get("$id", ""))
    match = re.search(r"/schemas/([^/]+)/", schema_id)
    if not match:
        raise AssertionError(f"schema $id has no version segment: {schema_id}")
    return match.group(1)


def deployment_image() -> str:
    import yaml

    deployment = yaml.safe_load(
        (ROOT / "deploy" / "kubernetes" / "deployment.yaml").read_text(encoding="utf-8")
    )
    return str(deployment["spec"]["template"]["spec"]["containers"][0]["image"])


def changelog_mentions(version: str) -> bool:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return bool(re.search(rf"^##\s+\[?{re.escape(version)}\]?]", text, re.MULTILINE))


def validate(*, release: bool = False, tag_version: str | None = None) -> None:
    version = package_version()

    assert schema_version() in ("v1alpha1",), (
        f"schema version {schema_version()!r} does not match the definition API"
    )

    image = deployment_image()
    assert ":latest" not in image, "deployment must not reference the :latest tag"
    assert version in image, (
        f"deployment image {image!r} does not pin the package version {version}"
    )

    if release:
        assert tag_version is not None
        assert tag_version == version, (
            f"tag {tag_version!r} does not match package version {version!r}"
        )
        assert changelog_mentions(version), (
            f"CHANGELOG.md has no section for version {version}; move the "
            "[Unreleased] entries into a versioned section before tagging"
        )


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    validate(release=tag is not None, tag_version=tag)
    print("release alignment ok")
