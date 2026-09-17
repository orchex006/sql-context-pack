# Versioning

| Surface | Current |
|---|---|
| Product, package, Skill, and all four host manifests | `2.1.0` |
| Output format | `2` |
| Requirement | `1.0` |
| SQLFluff | `4.2.2` |
| MCP SDK | `1.28.1` |

## Product version

The product follows SemVer.

- **Major** — an existing contract breaks: an MCP tool is removed or changes meaning, the
  output format version changes, the managed header shape changes, or a CLI command or option
  is removed.
- **Minor** — a backward-compatible surface is added: a new MCP tool, a new CLI option, a new
  host, or a new optional field.
- **Patch** — a bug, security or documentation fix that adds no new surface.

2.1.0 is the first released version. Versions 1.0.3 through 1.6.0 were internal development
iterations and were never published; their history is summarised by date in
[CHANGELOG.md](../CHANGELOG.md).

## Output format version

The managed file contract carries its own version, separate from the product version, because a
change to the header or layout affects files already on disk. It changes only when a managed
file's bytes would no longer validate under the previous rules.

## Requirement version

Requirement versions are additive. A new version retains the previous version's content in full
unless the owner explicitly changes or removes a rule. Every version ships a SHA-256 sidecar and
is stored with canonical LF bytes. See [Requirements](requirements.md).

## Keeping surfaces in agreement

These must all carry the same product version, and `scripts/validate_manifests.py` fails the
build if any of them drifts:

| Surface | Path |
|---|---|
| Package | `src/sqlctx/_version.py` |
| Build metadata | `pyproject.toml` |
| Skill | `skills/sql-context-pack/SKILL.md` |
| Codex | `.codex-plugin/plugin.json` |
| Claude Code | `.claude-plugin/plugin.json` and `marketplace.json` |
| Gemini CLI | `gemini-extension.json` |
| Antigravity | `.agents/plugin.json` and `plugins/marketplace.json` |
| This page | `docs/versioning.md` |

`docs/generated/*.json` must be regenerated from the code, not edited, whenever a contract
changes.

`CHANGELOG.md` records what shipped. It is not a substitute for the requirement history, and the
requirement history is not a substitute for it.
