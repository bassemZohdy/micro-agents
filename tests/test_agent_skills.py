"""The Agent Skills and harness manifests stay valid and in sync with the project."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import validate_skills as vs

GOOD_BODY = "# Title\n\nSome guidance.\n"


def _skill(root: Path, name: str, *, front: str | None = None, body: str = GOOD_BODY) -> Path:
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    if front is None:
        front = f"name: {name}\ndescription: Does a thing. Use when testing the validator.\n"
    path = folder / "SKILL.md"
    path.write_text(f"---\n{front}---\n{body}", encoding="utf-8")
    return path


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "ROOT", tmp_path)
    monkeypatch.setattr(vs, "SKILLS", tmp_path / "skills")
    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            ["[project]", 'version = "1.2.3"', "", "[project.optional-dependencies]", "real = []"]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_repository_skills_and_manifests_are_valid():
    assert vs.validate() == []


def test_every_skill_is_packaged_for_each_harness():
    names = {p.parent.name for p in (vs.ROOT / "skills").glob("*/SKILL.md")}
    assert len(names) >= 6
    for key in ("claude", "portable", "cursor", "gemini"):
        manifest = json.loads(vs.MANIFESTS[key].read_text(encoding="utf-8"))
        assert manifest["name"] == "micro-agents"
        assert manifest["version"] == vs.package_version()


def test_claude_plugin_root_is_the_repository_root():
    market = json.loads(vs.MANIFESTS["claude-marketplace"].read_text(encoding="utf-8"))
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [("micro-agents", "./")]
    # skills/ at the plugin root is what Claude Code, Codex and Cursor discover.
    assert (vs.ROOT / "skills").is_dir()


def test_valid_skill_passes(sandbox):
    path = _skill(sandbox, "micro-agents-ok")
    assert vs.check_skill(path) == []


@pytest.mark.parametrize(
    ("folder", "front", "fragment"),
    [
        (
            "micro-agents-a",
            "name: micro-agents-b\ndescription: x. Use when y.\n",
            "must equal its directory",
        ),
        ("micro-agents-a", "name: Micro_Agents-A\ndescription: x. Use when y.\n", "lowercase"),
        ("other-a", "name: other-a\ndescription: x. Use when y.\n", "must start with"),
        ("micro-agents-a", "name: micro-agents-a\n", "'description' is required"),
        (
            "micro-agents-a",
            "name: micro-agents-a\ndescription: only what, no trigger\n",
            "Use when",
        ),
        (
            "micro-agents-a",
            "name: micro-agents-a\ndescription: x. Use when y.\nextra: 1\n",
            "unknown frontmatter fields",
        ),
        (
            "micro-agents-a",
            "name: micro-agents-a\ndescription: x. Use when y.\nmetadata:\n  n: 1\n",
            "metadata must map strings",
        ),
        ("micro-agents-a", "name: micro-agents-a\ndescription: [unclosed\n", "not valid YAML"),
    ],
)
def test_invalid_frontmatter_is_reported(sandbox, folder, front, fragment):
    path = _skill(sandbox, folder, front=front)
    assert any(fragment in e for e in vs.check_skill(path))


def test_description_over_1024_characters_is_rejected(sandbox):
    front = "name: micro-agents-a\ndescription: " + ("x" * 1100) + " Use when y.\n"
    path = _skill(sandbox, "micro-agents-a", front=front)
    assert any("max 1024" in e for e in vs.check_skill(path))


def test_missing_frontmatter_and_long_body_are_rejected(sandbox):
    folder = sandbox / "skills" / "micro-agents-a"
    folder.mkdir(parents=True)
    bare = folder / "SKILL.md"
    bare.write_text("# no frontmatter\n", encoding="utf-8")
    assert any("missing YAML frontmatter" in e for e in vs.check_skill(bare))

    long_body = "line\n" * (vs.MAX_BODY_LINES + 5)
    path = _skill(sandbox, "micro-agents-b", body=long_body)
    assert any("exceeds" in e for e in vs.check_skill(path))


def _manifests(root: Path, version: str) -> None:
    (root / ".claude-plugin").mkdir()
    (root / ".cursor-plugin").mkdir()
    base = {"name": "micro-agents", "version": version, "description": "d"}
    entry = {"name": "micro-agents", "source": "./", "version": version}
    files = {
        ".claude-plugin/plugin.json": base,
        ".claude-plugin/marketplace.json": {
            "name": "m",
            "owner": {"name": "o"},
            "plugins": [entry],
        },
        "plugin.json": base,
        ".cursor-plugin/plugin.json": {**base, "skills": "./skills/"},
        "gemini-extension.json": {**base, "contextFileName": "AGENTS.md"},
    }
    for rel, data in files.items():
        (root / rel).write_text(json.dumps(data), encoding="utf-8")
    (root / "AGENTS.md").write_text("x", encoding="utf-8")
    (root / "skills").mkdir(exist_ok=True)


def _use_sandbox_manifests(root: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        vs,
        "MANIFESTS",
        {
            "claude": root / ".claude-plugin" / "plugin.json",
            "claude-marketplace": root / ".claude-plugin" / "marketplace.json",
            "portable": root / "plugin.json",
            "cursor": root / ".cursor-plugin" / "plugin.json",
            "gemini": root / "gemini-extension.json",
        },
    )


def test_manifests_in_sync_pass(sandbox, monkeypatch):
    _use_sandbox_manifests(sandbox, monkeypatch)
    _manifests(sandbox, "1.2.3")
    assert vs.check_manifests("1.2.3", {"micro-agents-a"}) == []


def test_version_drift_between_manifests_and_package_is_reported(sandbox, monkeypatch):
    _use_sandbox_manifests(sandbox, monkeypatch)
    _manifests(sandbox, "1.2.3")
    errors = vs.check_manifests("9.9.9", {"micro-agents-a"})
    # four manifests plus the marketplace entry
    assert sum("9.9.9" in e for e in errors) == 5


def test_missing_manifest_and_context_file_are_reported(sandbox, monkeypatch):
    _use_sandbox_manifests(sandbox, monkeypatch)
    _manifests(sandbox, "1.2.3")
    (sandbox / "plugin.json").unlink()
    (sandbox / "AGENTS.md").unlink()
    errors = vs.check_manifests("1.2.3", {"micro-agents-a"})
    assert any("plugin.json: missing" in e for e in errors)
    assert any("contextFileName" in e for e in errors)


def test_agents_md_index_must_match_the_skills(sandbox):
    (sandbox / "AGENTS.md").write_text(
        "| `micro-agents-a` | x |\n| `micro-agents-ghost` | y |\n".replace(" |\n", " |\n"),
        encoding="utf-8",
    )
    # the index regex expects "| `name` |"; both rows qualify
    errors = vs.check_index({"micro-agents-a", "micro-agents-b"})
    assert any("'micro-agents-b' is not listed" in e for e in errors)
    assert any("'micro-agents-ghost'" in e for e in errors)


@pytest.mark.parametrize(
    ("snippet", "fragment"),
    [
        ("See `docs/NOPE.md` for more.", "does not exist"),
        ("Set MICRO_AGENT_TOTALLY_MADE_UP_VARIABLE first.", "appears nowhere"),
        ('Run pip install -e ".[nonexistent]" now.', "not in pyproject.toml"),
        ("Run python tools/not_a_script.py now.", "does not exist"),
        ("Use `micro_agent.not_a_module` here.", "cannot be imported"),
    ],
)
def test_drift_between_a_skill_and_the_project_is_reported(sandbox, snippet, fragment):
    path = _skill(sandbox, "micro-agents-a", body=f"# T\n\n{snippet}\n")
    errors = vs.check_content(path, haystack="")
    assert any(fragment in e for e in errors), errors


def test_a_broken_definition_example_is_reported(sandbox):
    body = (
        "# T\n\n```yaml\napiVersion: microagents.io/v1alpha1\nkind: MicroAgent\n"
        "metadata: {name: x, version: 1.0.0}\nspec:\n  behavior: {nope: 1}\n```\n"
    )
    path = _skill(sandbox, "micro-agents-a", body=body)
    errors = vs.check_content(path, haystack="")
    assert any("definition example does not load" in e for e in errors)


def test_main_reports_success_and_failure(sandbox, capsys, monkeypatch):
    monkeypatch.setattr(vs, "validate", lambda: [])
    assert vs.main() == 0
    monkeypatch.setattr(vs, "validate", lambda: ["boom"])
    assert vs.main() == 1
    assert "boom" in capsys.readouterr().err
