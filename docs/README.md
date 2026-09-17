# Documentation map

This documentation set describes SQL Context Pack `2.1.0`, output format `2`, and
[Requirement v1.0](spec/design-spec-v1.0.md).

| Goal | Read |
|---|---|
| Go from nothing to a first export | [Getting Started](getting-started.md) |
| Install, upgrade, repair, uninstall | [Lifecycle](lifecycle.md) |
| Set up a specific host | [Codex](providers/codex.md) · [Claude Code](providers/claude-code.md) · [Antigravity](providers/antigravity.md) · [Gemini CLI](providers/gemini-cli.md) |
| See three worked flows | [Usage Examples](usage-examples.md) |
| Look up an owner CLI command | [Command Reference](command-reference.md) |
| Call the HTTP or MCP surface | [API and MCP](api-and-mcp.md) |
| Understand output files and headers | [Output Format](output-format.md) |
| Understand the design and trust boundaries | [Architecture](architecture.md) · [Security](security.md) |
| Fix something that is broken | [Troubleshooting](troubleshooting.md) |
| Repair or reinstall a component | [Knowledge base](knowledge/) |
| Work on the code | [Development](development.md) |
| Check requirement, version or state | [Requirements](requirements.md) · [Versioning](versioning.md) · [Implementation State](implementation-state.md) |
| Read what changed | [Changelog](../CHANGELOG.md) · [2.1.0 release notes](releases/2.1.0.md) |

Agents working on this repository start at [AGENTS.md](../AGENTS.md).

## Conventions

`generated/*.json` is produced from the code by `scripts/generate_contract_schemas.py`. Never
hand-edit it; regenerate it instead.

`spec/design-spec-v*.md` and its `.sha256` sidecar are the immutable requirement record. A
released requirement version is never edited in place; a new version is added beside it.
