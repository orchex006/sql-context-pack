# Command reference

`sqlctx` is the owner-local surface. The agent works through the Skill and MCP, and only ever
receives opaque IDs, relative paths and safe metadata.

## Product and diagnostics

| Command | Purpose |
|---|---|
| `sqlctx doctor [status\|version\|check\|update]` | Inspect versions and readiness, or update when `update` is given. Accepts `--host`, `--source`, `--online`, `--mcp`, `--version` |
| `sqlctx doctor update --host <host> --version <x.y.z>` | Update, pinned to an exact released tag |
| `sqlctx update --source <root>` | Update package, plugin and service from an owner-selected source |
| `sqlctx repair --source <root>` | Repair a broken lifecycle from a reviewed local build |
| `sqlctx service status\|start\|stop\|install\|remove` | Manage the one shared loopback service |
| `sqlctx runtime status` | Sanitized view of retained runtime state |
| `sqlctx audit tail [--limit 50]` | Recent operation audit records |
| `sqlctx approvals list` | Pending approval challenges |
| `sqlctx approvals grant [--challenge <id>]` | Grant one pending request, from an interactive terminal |

`doctor`, `doctor status`, `doctor version` and `doctor check` are strictly read-only. Only
`doctor update` installs anything.

## Profiles

| Command | Purpose |
|---|---|
| `sqlctx profile configure` | Create or edit an encrypted profile interactively |
| `sqlctx profile list` | Names, engine, readiness and safe scope |
| `sqlctx profile test <name>` | Sanitized driver, network and login check |
| `sqlctx profile schemas <name>` | Compare visible schemas against the allowlist |
| `sqlctx profile scope <name> --schema ... --object-type ...` | Set schemas, object types and exclusions |
| `sqlctx profile trust-certificate <name> --enable\|--disable` | Set SQL Server TLS trust explicitly |
| `sqlctx profile write-scope --profile <name> [--metadata-context-write] [--routine-write]` | Enable write scopes independently; both off by default |
| `sqlctx profile remove <name> --yes` | Remove a profile |

## Export and query

| Command | Purpose |
|---|---|
| `sqlctx export fetch --export-id <id> --destination <dir>` | Download a bundle over loopback HTTP and verify hashes |
| `sqlctx export assemble --bundle <zip> --output-root <dir>` | Merge batches, touching managed files only |
| `sqlctx validate output --root <dir>` | Re-read the written files and check the inventory |
| `sqlctx query <select> --profile <name>` | Read-only relational SELECT, masked Markdown output |
| `sqlctx sync-data` | Refresh retained eligible contexts without widening the original scope |
| `sqlctx format <file> --dialect <dialect>` | Format SQL to stdout without overwriting the source |

The CLI is also the way to get all-row output when an MCP result would be truncated.

## Registered folders

```text
sqlctx folder register --input-root <absolute-dir> --output-root <absolute-dir> --engine <engine>
sqlctx folder list
sqlctx folder plan --folder-id <id> [--resolve-file <relative.sql> --context <code> --description <text> --tag <tag>] [--in-place]
sqlctx folder apply --plan-id <id>
```

`plan` scans `.sql` recursively and writes nothing. `apply` rechecks the input hashes before
writing to the separate output root. `--in-place` is a privileged plan and needs an approval.

Registration stays CLI-only because it takes absolute paths.

## DB_METADATA_CONTEXT

SQL Server only. Other engines return `METADATA_CONTEXT_ENGINE_UNSUPPORTED`.

```text
sqlctx context-index list --profile <name> [--context <code>] [--tag <tag>] [--object-type <type>] [--status <status>] [--cursor <id>] [--limit 100]
sqlctx context-index sync-plan --profile <name> --plan-id <applied-folder-plan> --actor-id <number> --idempotency-key <key> [--complete-catalog-id <catalog>]
sqlctx context-index resolve --folder-id <id> --file <managed-relative.sql> --context <code> [--description <text>] [--tag <tag>]
sqlctx context-index generate-plan --profile <name> --folder-id <id> [--context <code>] [--tag <tag>] [--object-type <type>] [--include-unresolved]
```

`resolve` writes nothing to the database. It produces an immutable in-place plan that moves the
file out of `unknowns/`, writes the owner-confirmed header and preserves the SQL body. Take that
plan ID to `folder apply` (which needs an approval), then take the **same** plan ID to
`sync-plan`.

`sync-plan` chooses its mode from the payload. A plan carrying owner-sourced classifications, or
`--complete-catalog-id`, runs in `owner` mode and needs an approval; anything else runs as
`inventory` and does not. It reports the mode it used alongside the counts.

Use `--complete-catalog-id` only when the plan's identities match an `all` catalog that covered
every profile schema and type, had no include, exclude or profile exclusion, and had no analysis
failures. That mode soft-deactivates missing rows and reports `inserted`, `updated`, `unchanged`,
`deactivated` and `owner_values_preserved`. Without the flag it is a partial sync that
deactivates nothing.

If the proven catalog is empty you may omit `--plan-id` to deactivate an empty complete scope.

`generate-plan` returns metadata and relative paths only, and fails on index, header or body hash
drift.

## Procedure and function updates

```text
sqlctx routine plan --profile <name> --folder-id <id> [--file <relative.sql>] --idempotency-key <key> [--continue-on-error]
sqlctx routine apply --plan-id <id>
```

Omitting `--file` plans every managed procedure and function under the folder. Apply requires
`routine_write` plus an approval and is SQL Server only; other engines fail closed with
`ROUTINE_APPLY_ENGINE_UNSUPPORTED`.

## SQLFluff

```text
sqlctx sqlfluff status
sqlctx sqlfluff ensure
sqlctx sqlfluff update --version <x.y.z>
```

The pinned version is a guardrail, not a formatting preference — Query Data uses the same parser
to prove a query is read-only. See [the SQLFluff knowledge base](knowledge/sqlfluff.md).
