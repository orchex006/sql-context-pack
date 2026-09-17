# Knowledge — Hosts

Four supported hosts: `codex`, `claude`, `gemini`, `agy`.

**Never infer the host from the model name.** Read it from the environment or from what the owner
said. Claude Code is `claude`, Codex is `codex`, Antigravity is `agy`, legacy Gemini CLI is
`gemini`.

## Comparison

| | codex | claude | gemini | agy |
|---|---|---|---|---|
| Install unit | plugin | plugin | extension | file placement |
| Marketplace | yes | yes | no | no |
| Install CLI verb | `codex plugin add` | `claude plugin install` | `gemini extensions install` | none |
| Manifest | `.codex-plugin/plugin.json` | `.claude-plugin/plugin.json` | `gemini-extension.json` | `.agents/plugin.json` |
| Global skill path | `~/.codex/skills/` | `~/.claude/skills/` | `~/.gemini/extensions/` | `~/.gemini/config/skills/` |
| Workspace skill path | — | — | — | `<root>/.agents/skills/` |
| MCP config | `.mcp.json` | `.mcp.json` | `gemini-extension.json` | `~/.gemini/config/mcp_config.json` |
| Version source for doctor | native CLI listing | native CLI listing | native CLI listing | installed `SKILL.md` |

## agy (Antigravity)

Antigravity CLI is the official replacement for Gemini CLI, which Google deprecated in 2026. It
is a Go rewrite sharing an agent harness with the Antigravity desktop application.

**Important:** `agy` has no plugin or extension install CLI. Installation is purely file-based —
copy the skill directory into place and merge `mcp_config.json`.

### Skill

```text
Global:    ~/.gemini/config/skills/sql-context-pack/SKILL.md
Workspace: <project-root>/.agents/skills/sql-context-pack/SKILL.md
```

`SKILL.md` frontmatter needs at least `description`; `name` is optional and defaults to the
folder name. Antigravity still reads the legacy `.agent/skills` path, but `.agents/skills` is the
default.

### MCP

```text
Global:    ~/.gemini/config/mcp_config.json
Workspace: <project-root>/.agents/mcp_config.json
```

Local stdio server — what this package uses:

```json
{
  "mcpServers": {
    "sql-context-pack": {
      "command": "sqlctx-mcp-bridge",
      "args": [],
      "disabled": false
    }
  }
}
```

Supported stdio keys: `command` (required), `args`, `env`, `cwd`, `disabled`, `disabledTools`.

Remote servers use `serverUrl`, **not** the `url` key used by Cursor and VS Code. This package
does not use that mode: the bridge is stdio and reaches the service over loopback itself. Never
configure Antigravity to hit the HTTP service directly — that skips the per-session profile
isolation in `SessionProfileRouter`.

This file is shared with every other MCP server the owner uses, so writes to it must be a merge.
Install and update preserve other entries; uninstall removes only `sql-context-pack`.

### Checking inside Antigravity

Type `/mcp` in the prompt panel for the MCP Manager overlay: server status, manual reload, and
connection logs.

## gemini (legacy)

Still supported and still maintained here, but deprecated for general users. Google stopped
serving Gemini CLI for consumer accounts (AI Pro, Ultra, free tier) on 18 June 2026. Gemini Code
Assist Standard and Enterprise licences and paid API keys continue to work.

Gemini loads **extensions**, not bare skills. A directory without `gemini-extension.json` is
never discovered, which is why `--mode skill` is refused for this host.

If the owner asks what to use: recommend `agy`, state the status accurately, and do not push them
to migrate.

## codex and claude

Both use a marketplace plus a native plugin CLI:

```bash
codex plugin add sql-context-pack@<marketplace>
claude plugin install sql-context-pack@<marketplace>
```

`doctor` verifies the version through `<cli> plugin list --json`, matching an entry whose `name`
is `sql-context-pack` or whose `pluginId` starts with `sql-context-pack@`, and which is enabled.

`PLUGIN_VERSION_UNVERIFIED` means that listing could not be read — possibly a cache-only install
with no local staging metadata. It does not by itself mean the plugin is broken. Check
`<cli> plugin list` directly before concluding anything.

## Updating

```bash
sqlctx doctor update --host <codex|claude|gemini|agy> [--version <x.y.z>]
```

- No `--version` installs the latest release.
- `--version` must be a tag that exists in `orchex006/sql-context-pack`. If it does not, stop.
  **Never** fall back to latest silently.
- The service is shared by every host. Upgrading it benefits all of them — **do not** reinstall
  another host's plugin the owner did not ask about.
- Open a new session afterwards; Skill instructions load at session start.
