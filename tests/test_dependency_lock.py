"""Guard the runtime dependency lock and its container integration."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOCKFILE = ROOT / "requirements.txt"
PACKAGE_LINE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*==\S+", re.MULTILINE)


def _requirement_blocks() -> list[str]:
    text = LOCKFILE.read_text(encoding="utf-8")
    return [
        block for block in re.split(r"\n(?=[A-Za-z0-9][A-Za-z0-9_.-]*==)", text) if "==" in block
    ]


def test_runtime_lock_is_generated_with_hashes_and_exact_versions():
    text = LOCKFILE.read_text(encoding="utf-8")
    assert "--generate-hashes" in text.splitlines()[1]

    blocks = _requirement_blocks()
    assert len(blocks) >= 20
    assert all(PACKAGE_LINE.search(block) for block in blocks)
    assert all("--hash=sha256:" in block for block in blocks)


def test_dockerfile_installs_only_from_the_hash_pinned_runtime_lock():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt ./" in dockerfile
    assert "pip install --no-cache-dir --require-hashes -r requirements.txt" in dockerfile
    assert "pip install --no-cache-dir --no-deps ." in dockerfile
