# Agent Skills and Plugins

Micro-Agents ships task playbooks for AI coding agents, so an assistant working
in (or with) this project follows its conventions instead of guessing. They are
written once, in the open [Agent Skills](https://agentskills.io/specification)
format, and packaged for each harness with a thin manifest.

## Skills

| Skill | Use it to |
|---|---|
| `micro-agents-author-definition` | write, validate and review an agent definition YAML |
| `micro-agents-run-and-deploy` | run the service, call the HTTP API, bind model and state, containers, Kubernetes |
| `micro-agents-choose-runtime` | pick `custom`, `google-adk` or `langgraph`, switch safely, read the capability table |
| `micro-agents-govern-tools` | classify tool side effects, policy, approvals, MCP, idempotency, checkpoints, resilience |
| `micro-agents-add-runtime-adapter` | add a runtime and opt it into the governance layer |
| `micro-agents-contribute` | change this repository: gates, tests, docs, schema, changelog |

A harness loads only each skill's short description until the task matches, so
all six together add roughly 900 tokens to a session; a skill's body (about 2,000
to 3,000 tokens) is read only when it is used.

The repository also carries `AGENTS.md` (read by Codex, Cursor, Copilot and
others) and `CLAUDE.md` (which imports it): a project map, the commands, and the
rules that are easy to break.

## Install

The plugin is the repository itself, so each harness installs it from the
GitHub repository's default branch.

### Claude Code

```bash
claude plugin marketplace add bassemZohdy/micro-agents
claude plugin install micro-agents@micro-agents
```

Inside a session the equivalents are `/plugin marketplace add
bassemZohdy/micro-agents` and `/plugin install micro-agents@micro-agents`.
To try a checkout without installing: `claude --plugin-dir .`.

### Codex

```bash
codex plugin marketplace add bassemZohdy/micro-agents
codex plugin add micro-agents@micro-agents
```

Codex reads the same `.claude-plugin/marketplace.json`; no separate
marketplace file is needed.

### Gemini CLI

```bash
gemini extensions install https://github.com/bassemZohdy/micro-agents
```

For local development use `gemini extensions link .`. The extension
loads `AGENTS.md` as its context file and discovers `skills/` automatically.

### Cursor

The repository has a `.cursor-plugin/plugin.json` that follows Cursor's
documented plugin format and a root `plugin.json` for the portable Agent
Plugins standard. These have **not** been exercised in Cursor itself; if
plugin import does not work for you, copy the folders from `skills/` into
the skills directory Cursor documents.

### Any other harness

Anything that reads the Agent Skills format can use the folders in
`skills/` directly: copy or symlink them into that harness's skills
directory (user-level or project-level). Each folder is self-contained
apart from links to this documentation site.

## How the pieces fit

```text
skills/<name>/SKILL.md          single source of truth (open Agent Skills format)
AGENTS.md                       project context for any harness (CLAUDE.md imports it)
.claude-plugin/plugin.json      Claude Code plugin (also read by Codex's marketplace)
.claude-plugin/marketplace.json   marketplace whose one plugin is the repository root ("./")
plugin.json                     portable Agent Plugins manifest (Codex, Cursor)
.cursor-plugin/plugin.json      Cursor plugin
gemini-extension.json           Gemini CLI extension (context file: AGENTS.md)
```

Every manifest carries the package version from `pyproject.toml`.

## Quality gates

`python tools/validate_skills.py` (also run by `tests/test_agent_skills.py`, so
it runs in the normal test job, and as a step of the `docs` job) checks:

- each `SKILL.md` follows the specification: `name` equals its folder, lowercase
  letters, digits and single hyphens, a `description` of at most 1024 characters
  that says when to use the skill, only known frontmatter fields, a body of at
  most 500 lines;
- every manifest parses, names the plugin `micro-agents`, and has the same
  version as `pyproject.toml`; the marketplace entry points at the repository
  root; the Gemini context file exists;
- a skill cannot drift from the project: repository paths, install extras,
  `MICRO_AGENT_*` variables, helper scripts and Python modules it mentions must
  exist, and every complete YAML definition in a skill must load with the real
  loader;
- `AGENTS.md` lists exactly the skills that exist.

The Claude Code, Codex and Gemini CLI packaging was additionally exercised with
each tool: `claude plugin validate` and `claude --plugin-dir`, a Codex
marketplace add and plugin install into a scratch `CODEX_HOME`, and `gemini
extensions validate`.

## Changing a skill

1. Edit `skills/<name>/SKILL.md`. Prefer linking to the documentation over
   copying it; keep the body short and put long material in a `references/`
   folder beside it.
2. Run `python tools/validate_skills.py`. A failure names the file and the
   problem.
3. When the version changes, update `pyproject.toml` and all manifests together;
   the validator fails until they agree.
4. Add a skill by creating `skills/micro-agents-<topic>/SKILL.md` and listing it
   in the table in `AGENTS.md`.
