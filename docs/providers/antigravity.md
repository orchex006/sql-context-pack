# Antigravity (`agy`)

Antigravity CLI is Google's official successor to Gemini CLI, which stopped serving consumer
accounts in June 2026. The two are separate hosts here, with separate paths and separate
manifests — installing one does not install the other.

Antigravity has **no plugin or extension install command**. Discovery is file placement plus one
shared MCP configuration file, so the install unit is the Skill directory and a JSON merge.

## Paths

| Scope | Skill | MCP config |
|---|---|---|
| Global | `~/.gemini/config/skills/sql-context-pack/` | `~/.gemini/config/mcp_config.json` |
| Workspace | `<project>/.agents/skills/sql-context-pack/` | `<project>/.agents/mcp_config.json` |

Antigravity still reads the legacy `.agent/skills` location, but `.agents/skills` is the default
and is what this repository ships.

## Install

```bash
sqlctx doctor update --host agy
```

That stages `skills/sql-context-pack/` into the global skill path and merges the bridge entry
into `mcp_config.json`.

The merge is additive in both directions: other MCP servers in that file survive install and
update, and uninstall removes only the `sql-context-pack` entry.

Only the skill layout is accepted. The plugin layout would bury `SKILL.md` one directory below
where Antigravity looks, so it is refused with `PLUGIN_MODE_UNSUPPORTED` rather than installing
something that silently never loads.

## MCP entry

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

This is a **stdio** server: Antigravity launches `sqlctx-mcp-bridge`, and the bridge connects to
the one shared loopback service.

Do not configure Antigravity to reach the HTTP service directly with `serverUrl`. The bridge is
what gives each session its own active profile; bypassing it makes every window share one.

Antigravity uses `serverUrl` for remote servers, not the `url` key used by Cursor and VS Code.
That difference does not affect this package, which is stdio-only.

## Use it

There is no repository-specific slash command. Ask in natural language, naming the Skill:

```text
Use the sql-context-pack skill to run setup.
Use the sql-context-pack skill to list profiles.
Use the sql-context-pack skill to connect profile <profile-name>.
```

Type `/mcp` in the prompt panel for the MCP Manager overlay, which shows server status, allows a
manual reload, and displays connection logs.

## Verify

```bash
sqlctx doctor check --host agy
```

Because there is no native plugin listing to query, the reported version comes from the installed
`SKILL.md` frontmatter rather than a manifest. Expect 34 core MCP tools plus 4 bridge tools.

Reopen the Antigravity session after an update; Skill instructions are read at session start.

## Uninstall

```bash
sqlctx service remove
python3 -m pip uninstall sql-context-pack
```

Then delete `~/.gemini/config/skills/sql-context-pack/` and remove the `sql-context-pack` entry
from `~/.gemini/config/mcp_config.json`, leaving the other entries alone.
