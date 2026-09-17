# SQL Context Pack

SQL Context Pack turns a live database into AI-ready SQL context: the DDL and metadata of every
`TABLE`, `PROCEDURE` and `FUNCTION` the owner's profile allows, plus a bounded masked sample of
table data, organised into context folders — with anything it cannot confidently classify kept
in `unknowns/` rather than guessed.

Version `2.1.0` · Python `>=3.11` · Output format `2`

## What it does

- Reads SQL Server, PostgreSQL, MySQL, MariaDB and Oracle, read-only.
- Exports DDL and metadata for tables, stored procedures and stored functions. Table *data* is
  only ever a bounded, masked sample — never every row.
- Writes a managed header into every SQL file carrying identity, context, description, tags,
  classification status and content hashes.
- Classifies files from an owner-registered folder, writing to a separate output directory by
  default.
- Maintains `[agrimap_app].[DB_METADATA_CONTEXT]` as a searchable context index, synchronised on
  every export so the next run starts better informed.
- Plans and applies procedure and function updates, one file or a whole folder, always through
  `CREATE OR ALTER` and always behind an approval.
- Runs the same Skill and MCP surface on Codex, Claude Code, Antigravity (`agy`) and Gemini CLI.
- Runs **one** service per machine. Every session connects through its own stdio bridge, not its
  own server.

## Boundaries worth knowing up front

- Credentials stay on the owner's side. The agent never receives them.
- The service binds loopback only.
- Both database write scopes are off by default, and are independent.
- Supervised database writes require a single-use, request-bound approval.
- Query Data is read-only SELECT, validated by parsing, with per-column masking.
- The context index and routine deployment are SQL Server features. The other four engines
  export full context and support Query Data, and say so plainly rather than failing oddly.

## Start here

1. [Getting Started](docs/getting-started.md) — from nothing to a first export.
2. Pick your host: [Codex](docs/providers/codex.md) ·
   [Claude Code](docs/providers/claude-code.md) ·
   [Antigravity](docs/providers/antigravity.md) ·
   [Gemini CLI](docs/providers/gemini-cli.md)
3. [Usage Examples](docs/usage-examples.md) — three worked flows, simple to advanced.

The full [documentation map](docs/README.md) lists everything else.

## Version 2.1.0

2.1.0 is the first released version; the repository history begins there. It closes three Query
Data read-path bypasses, makes the context index cover every discovered object on every export,
adds the Antigravity host, and makes the shared service manageable from an installed package on
every platform.

It is a major version because `sqlctx_sync_context_index` gained a `mode` and Query Data now
rejects `FOR XML`/`FOR JSON`, table and query hints, and bare identity functions. Read the
[release and migration notes](docs/releases/2.1.0.md) before upgrading, and
[CHANGELOG.md](CHANGELOG.md) for the full dated history.

## Everyday commands

```bash
sqlctx doctor check --mcp          # is everything ready?
sqlctx doctor update --host <host> # explicit update
sqlctx service status              # which supervisor owns the shared service
```

Working on this repository? Start at [AGENTS.md](AGENTS.md); repair procedures are in
[docs/knowledge/](docs/knowledge/).
