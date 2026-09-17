# Getting Started

From an empty machine to your first SQL context bundle. The same runtime and Skill serve all
four hosts.

## 1. Install the host integration

Pick exactly one and follow it through:

- [Codex](providers/codex.md)
- [Claude Code](providers/claude-code.md)
- [Antigravity (`agy`)](providers/antigravity.md)
- [Gemini CLI](providers/gemini-cli.md)

Then open a **new** session — Skill instructions load at session start — and confirm readiness:

```bash
sqlctx doctor check --host <codex|claude|agy|gemini>
```

Report success only when package, plugin and service versions all match. See
[Lifecycle](lifecycle.md).

Each host has its own invocation syntax. Do not carry one host's syntax to another.

## 2. Create a profile

```bash
sqlctx profile configure
sqlctx profile list
sqlctx profile test agrimap-dev
```

A profile stores connection values encrypted and exposes only its name to the agent.

Set `allowed_schemas` and `allowed_object_types` to include `TABLE`, `PROCEDURE` and `FUNCTION`
if you want complete capture. A profile created before function support keeps its stored
allowlist and is never widened automatically — if `FUNCTION` is missing, update the profile
scope yourself.

## 3. Connect the profile in a session

Each session has its own active profile; connecting in one window does not affect another.

| Host | How |
|---|---|
| Codex | `$sql-context-pack profiles` then `$sql-context-pack connect agrimap-dev` |
| Claude Code | `/sql-context-pack:sql-context-pack profiles` then `... connect agrimap-dev` |
| Antigravity | `/mcp` to confirm the server, then ask in natural language |
| Gemini CLI | `/skills list`, then "Use the sql-context-pack skill to list profiles." |

Antigravity and Gemini CLI have no repository-specific slash command; ask in natural language.

## 4. Request complete context

Ask the agent to build all context using `selection.mode=all` with no include filter. Discovery
still stays inside the profile's schemas, object types and exclusions.

What "complete" means:

| Object | Captured |
|---|---|
| `TABLE` | DDL, column/constraint/index metadata, and a bounded masked sample — **never every row** |
| `PROCEDURE` | Sanitized definition. On SQL Server the executable body begins `CREATE OR ALTER PROCEDURE` |
| `FUNCTION` | Sanitized definition and dependencies |

Confirmed objects land in `<context>/tables|store_procedures|functions/`. Objects whose context
could not be confirmed are still exported in full, under `unknowns/`, with empty context and
tags. Nothing is guessed.

The agent returns the commands to run on your side:

```bash
sqlctx export fetch ...
sqlctx export assemble ...
sqlctx validate output ...
```

The run also reports discovered, analyzed, failed, materialized, excluded, security-skipped and
unresolved counts separately. Those numbers should reconcile; if they do not, ask why before
trusting the output.

## 5. Read the managed header

The first line of every managed SQL file is `-- sqlctx-context: {json}` carrying object identity,
engine, context, description, tags, classification status and source, evidence, source and
content hashes, header version and output format version.

Removing that line must leave the normalized SQL body unchanged — that is what the content hash
checks.

## 6. Classify an existing folder

The owner registers absolute paths once; the agent then sees only a folder ID.

```bash
sqlctx folder register --input-root D:\sql\incoming --output-root D:\sql\classified --engine sqlserver
sqlctx folder plan --folder-id <folder-id>
sqlctx folder apply --plan-id <plan-id>
```

Output goes to a separate directory by default. Files whose context is unknown go to
`unknowns/`.

Supply an owner decision with `--resolve-file`, `--context`, `--description` and `--tag`. The
scanner uses the same deterministic exact-name, prefix and schema rules as the catalog, and
confirms a context only when exactly one matches. Colliding rules or thin evidence stay in
`unknowns/`.

`--in-place` requires an owner approval.

## 7. Set up the database context index

This step is SQL Server only. On PostgreSQL, MySQL, MariaDB and Oracle the index returns
`METADATA_CONTEXT_ENGINE_UNSUPPORTED`; context files still export normally.

Your DBA reviews and deploys
[`sql/DB_METADATA_CONTEXT/table/DB_METADATA_CONTEXT.sql`](../sql/DB_METADATA_CONTEXT/table/DB_METADATA_CONTEXT.sql).
Installation never connects to a real database.

Then enable the write scope once:

```bash
sqlctx profile write-scope --profile agrimap-dev --metadata-context-write
```

That opt-in is all the routine case needs. From then on every export synchronises the full
discovered inventory automatically, including objects it could not classify, which are recorded
as `unresolved`. That is what makes the next run better informed — and it is why the table should
fill up rather than holding a handful of hand-resolved rows.

The agent then brings you one consolidated list of every unresolved object with a suggested
context and the evidence behind it. Those suggestions are never promoted on their own.

To record a decision, resolve through a plan so the path, header and index stay consistent:

```bash
sqlctx context-index resolve --folder-id <folder-id> --file unknowns/functions/dbo_F.sql \
  --context content --description "Content helper" --tag content --tag share
sqlctx folder apply --plan-id <resolution-plan-id>
sqlctx context-index sync-plan --profile agrimap-dev --plan-id <resolution-plan-id> \
  --actor-id 123 --idempotency-key resolve-20260917
```

An owner-mode sync returns an approval challenge the first time. Run `sqlctx approvals grant`,
then retry the identical request.

Add `--complete-catalog-id <catalog-id>` only to reconcile the whole index. Rows missing from the
catalog are soft-deactivated only when that catalog was all-mode, matched the profile scope
exactly, had no filters or exclusions, and analyzed everything successfully. A partial sync never
deactivates anything.

The schema verifier checks types, sizes, nullability, identity, defaults, checks (including
header and output version) and indexes before every write, and rejects surplus constraints,
indexes, triggers or foreign keys and any index direction that differs from the reviewed DDL.

## 8. Plan a routine update

`metadata_context_write` and `routine_write` are independent and both default to off. Always plan
before applying.

```bash
sqlctx profile write-scope --profile agrimap-dev --metadata-context-write --routine-write
sqlctx routine plan --profile agrimap-dev --folder-id <folder-id> \
  --file app_state/store_procedures/P.sql --idempotency-key routine-20260917
sqlctx routine apply --plan-id <plan-id>
```

Omit `--file` to plan every procedure and function in the folder. Apply is SQL Server only; other
engines return `ROUTINE_APPLY_ENGINE_UNSUPPORTED`, and there is no DROP-and-recreate fallback.
