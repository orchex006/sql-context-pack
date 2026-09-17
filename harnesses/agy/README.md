# Antigravity (`agy`) packaging

Antigravity CLI and the Antigravity IDE share one configuration root under `~/.gemini/config`.
Unlike Codex, Claude Code and Gemini CLI, Antigravity has **no plugin or extension install
command** — discovery is purely file placement, so the install unit is the Skill directory plus
one merge into the shared MCP config.

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
into `mcp_config.json`. The merge is additive: other MCP servers in that file are left alone,
and uninstall removes only our own entry.

## MCP entry

See [`mcp_config.json.example`](mcp_config.json.example). It is a **stdio** server — Antigravity
launches `sqlctx-mcp-bridge`, and the bridge connects to the one shared loopback service.

Do not configure Antigravity to reach the HTTP service directly with `serverUrl`. The bridge is
what gives each session its own active profile (`SessionProfileRouter`); bypassing it makes every
Antigravity window share one profile.

Note that Antigravity uses `serverUrl` for remote servers, not the `url` key used by Cursor and
VS Code. That difference does not affect this package, which is stdio-only.

## Verify

```bash
sqlctx doctor check --host agy
```

Because there is no native listing to query, the reported plugin version comes from the staged
`.agents/plugin.json`. Inside Antigravity, `/mcp` opens the MCP Manager overlay with server
status and connection logs.

Expect 34 core MCP tools plus 4 bridge tools. Reopen the Antigravity session after an update;
Skill instructions are read at session start.

## Relationship to Gemini CLI

`agy` is the official successor to Gemini CLI, which Google stopped serving for consumer
accounts in June 2026. The two are separate hosts here with separate paths and separate
manifests — installing one does not install the other. `--host gemini` remains supported for
owners on Gemini Code Assist Standard/Enterprise or a paid API key.
