"""Validate the Agent Skills and the harness plugin manifests.

The skills in ``skills/`` are the single source of truth. Each harness gets a
thin manifest that points at them (Claude Code, Codex and the portable Agent
Plugins standard, Gemini CLI, Cursor). This module checks, with no network:

- every ``SKILL.md`` follows the open Agent Skills format
  (https://agentskills.io/specification): required ``name`` that equals the
  directory name, ``description`` of at most 1024 characters, only known
  frontmatter fields, a body of at most 500 lines;
- every manifest parses and agrees on the plugin name and on the package
  version in ``pyproject.toml``;
- the skills cannot silently drift from the project: repository paths, extras,
  ``MICRO_AGENT_*`` variables, helper scripts, Python modules and YAML examples
  that a skill mentions must exist or load;
- ``AGENTS.md`` lists exactly the skills that exist.

Run ``python tools/validate_skills.py``; exit status 1 lists every problem.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
SKILLS = ROOT / "skills"
PLUGIN_NAME = "micro-agents"
SKILL_PREFIX = "micro-agents-"

ALLOWED_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)
MAX_BODY_LINES = 500

MANIFESTS = {
    "claude": ROOT / ".claude-plugin" / "plugin.json",
    "claude-marketplace": ROOT / ".claude-plugin" / "marketplace.json",
    "portable": ROOT / "plugin.json",
    "cursor": ROOT / ".cursor-plugin" / "plugin.json",
    "gemini": ROOT / "gemini-extension.json",
}

#: Backticked repository paths a skill may cite; each must exist.
PATH_RE = re.compile(
    r"`((?:docs|examples|deploy|tools|tests|runtimes|micro_agent|cloud|skills)/[A-Za-z0-9_./*<>\-]+"
    r"|[A-Z][A-Za-z_]*\.md|pyproject\.toml|mkdocs\.yml)`"
)
ENV_RE = re.compile(r"\bMICRO_AGENT_[A-Z0-9_]+\b")
EXTRA_RE = re.compile(r'"?\.\[([a-z0-9,\-]+)\]"?')
SCRIPT_RE = re.compile(r"python (tools/[a-z_]+\.py)")
MODULE_RE = re.compile(r"`(micro_agent(?:\.[a-z_]+)+)`")
#: Names that look like paths or modules but are not: the skill file name itself and
#: the entry-point groups (``[project.entry-points."micro_agent.tools"]``).
NOT_PATHS = {"SKILL.md"}
ENTRY_POINT_GROUPS = {"micro_agent.tools", "micro_agent.runtimes"}
FENCE_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def package_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def package_extras() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"].get("optional-dependencies", {}))


def parse_skill(path: Path) -> tuple[dict[str, object], str, list[str]]:
    """Return ``(frontmatter, body, errors)`` for one ``SKILL.md``."""
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, [f"{path}: missing YAML frontmatter delimited by ---"]
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return {}, match.group(2), [f"{path}: frontmatter is not valid YAML ({exc})"]
    if not isinstance(meta, dict):
        return {}, match.group(2), [f"{path}: frontmatter must be a mapping"]
    return meta, match.group(2), errors


def check_skill(path: Path) -> list[str]:
    meta, body, errors = parse_skill(path)
    if errors:
        return errors
    label = str(path.relative_to(ROOT))
    unknown = set(meta) - ALLOWED_FIELDS
    if unknown:
        errors.append(f"{label}: unknown frontmatter fields {sorted(unknown)}")
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        errors.append(f"{label}: 'name' is required")
    else:
        if len(name) > 64 or not NAME_RE.match(name):
            errors.append(
                f"{label}: name {name!r} must be lowercase a-z0-9 and single hyphens, max 64"
            )
        if name != path.parent.name:
            errors.append(f"{label}: name {name!r} must equal its directory {path.parent.name!r}")
        if not name.startswith(SKILL_PREFIX):
            errors.append(f"{label}: name must start with {SKILL_PREFIX!r} to avoid collisions")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{label}: 'description' is required")
    elif len(description) > 1024:
        errors.append(f"{label}: description is {len(description)} characters (max 1024)")
    elif "use when" not in description.lower():
        errors.append(f"{label}: description should say when to use the skill ('Use when ...')")
    compat = meta.get("compatibility")
    if compat is not None and (not isinstance(compat, str) or not 1 <= len(compat) <= 500):
        errors.append(f"{label}: compatibility must be a string of 1-500 characters")
    metadata = meta.get("metadata")
    if metadata is not None and not (
        isinstance(metadata, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items())
    ):
        errors.append(f"{label}: metadata must map strings to strings")
    if body.count("\n") > MAX_BODY_LINES:
        errors.append(f"{label}: body exceeds {MAX_BODY_LINES} lines; move detail to references/")
    if not body.strip():
        errors.append(f"{label}: body is empty")
    return errors


def _repo_text_haystack() -> str:
    parts: list[str] = []
    for folder in ("micro_agent", "runtimes", "cloud", "docs", "tests", "tools"):
        for file in (ROOT / folder).rglob("*"):
            if file.suffix in {".py", ".md"} and file.is_file():
                parts.append(file.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def check_content(path: Path, haystack: str) -> list[str]:
    """Guard a skill against drifting away from the repository it describes."""
    text = path.read_text(encoding="utf-8")
    label = str(path.relative_to(ROOT))
    errors: list[str] = []

    for ref in sorted(set(PATH_RE.findall(text))):
        if ref in NOT_PATHS or any(token in ref for token in ("<", "*")):
            continue
        if not (ROOT / ref.rstrip("/")).exists():
            errors.append(f"{label}: cites {ref!r} which does not exist")

    for var in sorted(set(ENV_RE.findall(text))):
        if var not in haystack:
            errors.append(f"{label}: mentions {var} which appears nowhere in the project")

    extras = package_extras()
    for group in EXTRA_RE.findall(text):
        for extra in group.split(","):
            if extra and extra not in extras:
                errors.append(f"{label}: install extra {extra!r} is not in pyproject.toml")

    for script in sorted(set(SCRIPT_RE.findall(text))):
        if not (ROOT / script).exists():
            errors.append(f"{label}: runs {script!r} which does not exist")

    for module in sorted(set(MODULE_RE.findall(text)) - ENTRY_POINT_GROUPS):
        try:
            importlib.import_module(module)
        except ImportError as exc:
            errors.append(f"{label}: module {module!r} cannot be imported ({exc})")

    errors.extend(_check_yaml_examples(text, label))
    return errors


def _check_yaml_examples(text: str, label: str) -> list[str]:
    from micro_agent.definition import DefinitionError, load_definition_from_yaml

    errors: list[str] = []
    for block in FENCE_RE.findall(text):
        if "apiVersion:" not in block:
            continue
        try:
            load_definition_from_yaml(block)
        except (DefinitionError, yaml.YAMLError, ValueError) as exc:
            errors.append(f"{label}: a full definition example does not load ({exc})")
    return errors


def check_manifests(version: str, skill_names: set[str]) -> list[str]:
    errors: list[str] = []
    loaded: dict[str, dict[str, object]] = {}
    for key, path in MANIFESTS.items():
        label = str(path.relative_to(ROOT))
        if not path.exists():
            errors.append(f"{label}: missing")
            continue
        try:
            loaded[key] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{label}: invalid JSON ({exc})")

    for key in ("claude", "portable", "cursor", "gemini"):
        manifest = loaded.get(key)
        if manifest is None:
            continue
        label = str(MANIFESTS[key].relative_to(ROOT))
        if manifest.get("name") != PLUGIN_NAME:
            errors.append(f"{label}: name must be {PLUGIN_NAME!r}")
        if manifest.get("version") != version:
            errors.append(
                f"{label}: version {manifest.get('version')!r} != pyproject version {version!r}"
            )
        if not manifest.get("description"):
            errors.append(f"{label}: description is required")

    market = loaded.get("claude-marketplace")
    if market is not None:
        label = str(MANIFESTS["claude-marketplace"].relative_to(ROOT))
        plugins = market.get("plugins")
        if not isinstance(plugins, list) or len(plugins) != 1:
            errors.append(f"{label}: expected exactly one plugin entry")
        else:
            entry = plugins[0]
            if entry.get("name") != PLUGIN_NAME or entry.get("source") != "./":
                errors.append(f"{label}: plugin entry must be {PLUGIN_NAME!r} with source './'")
            if entry.get("version") != version:
                errors.append(f"{label}: entry version {entry.get('version')!r} != {version!r}")
        if not isinstance(market.get("owner"), dict):
            errors.append(f"{label}: owner is required")

    cursor = loaded.get("cursor")
    if (
        cursor is not None
        and not (ROOT / str(cursor.get("skills", "skills")).lstrip("./")).is_dir()
    ):
        errors.append(".cursor-plugin/plugin.json: 'skills' path does not exist")

    gemini = loaded.get("gemini")
    if gemini is not None:
        context = gemini.get("contextFileName", "GEMINI.md")
        if not (ROOT / str(context)).is_file():
            errors.append(f"gemini-extension.json: contextFileName {context!r} does not exist")

    if not skill_names:
        errors.append("skills/: no skills found")
    return errors


def check_index(skill_names: set[str]) -> list[str]:
    """``AGENTS.md`` must list exactly the skills that exist."""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"\| `(micro-agents-[a-z0-9-]+)` \|", text))
    errors = [f"AGENTS.md: skill {n!r} is not listed" for n in sorted(skill_names - listed)]
    errors += [
        f"AGENTS.md: lists {n!r} which has no skills/ folder" for n in sorted(listed - skill_names)
    ]
    return errors


def validate() -> list[str]:
    errors: list[str] = []
    skill_files = sorted(SKILLS.glob("*/SKILL.md"))
    skill_names = {p.parent.name for p in skill_files}
    haystack = _repo_text_haystack()
    for path in skill_files:
        found = check_skill(path)
        errors.extend(found)
        if not found:
            errors.extend(check_content(path, haystack))
    errors.extend(check_manifests(package_version(), skill_names))
    errors.extend(check_index(skill_names))
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("skill validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    count = len(list(SKILLS.glob("*/SKILL.md")))
    print(f"skills ok ({count} skills, {len(MANIFESTS)} manifests, version {package_version()})")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
