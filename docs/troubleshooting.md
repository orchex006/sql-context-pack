# Troubleshooting

Start with `sqlctx doctor --host <host>` and read every entry in `findings[]` before changing
anything. Repair procedures live in [the knowledge base](knowledge/).

## The Skill loads but tools are missing

Expect 34 core MCP tools plus 4 bridge tools. If the count differs, the layers are at different
revisions.

Check `sqlctx doctor`, update plugin, owner package and service together via
[Lifecycle](lifecycle.md), then open a new session — the tool list is read at session start.

Never start a second server or bridge inside an existing session; it contends for the same
loopback port.

## Complete capture has no FUNCTION

```bash
sqlctx profile list
sqlctx profile scope
```

An older profile may allow only tables and procedures. The allowlist is never widened
automatically. After widening scope, create a new catalog.

## Files are sitting in unknowns/

This is not an export failure. The definition was captured; its context was not confirmed, and
nothing was guessed.

Resolve through a plan so path, header and index stay consistent:

```bash
sqlctx folder plan --resolve-file ... --context ...          # at first classification
sqlctx context-index resolve --folder-id ... --file unknowns/... --context ...   # already applied
sqlctx folder apply --plan-id <resolution-plan-id>
sqlctx context-index sync-plan --profile <name> --plan-id <resolution-plan-id> ...
```

Never assign a context by guessing from the filename.

## DB_METADATA_CONTEXT has far fewer rows than expected

From 2.1.0 every export synchronises the full discovered inventory, so the table should cover
the catalog, not a handful of hand-resolved rows.

If it is nearly empty, check in this order:

1. Is the engine SQL Server? Other engines return `METADATA_CONTEXT_ENGINE_UNSUPPORTED`.
2. Is `metadata_context_write` enabled? It is a one-time opt-in:
   `sqlctx profile write-scope --profile <name> --metadata-context-write`
3. Did the agent report submitted count against discovered count? If those differ it should say
   which objects were left out.
4. On a catalog larger than 5,000 objects, sync is batched. Every batch must be sent; the counts
   are the sum across batches.

Unresolved rows are expected and correct — they are how the index records an object whose
context nobody has confirmed yet.

## MANAGED_SQL_HEADER_INVALID / CONTENT_DRIFT

Never hand-edit a header or hash. Compare the SQL body against its source and create a new
folder or export plan.

The header accepts only the output v2 shape, and the content hash must match the body with the
first line removed.

## METADATA_CONTEXT_SCHEMA_DRIFT

`[agrimap_app].[DB_METADATA_CONTEXT]` is missing, or its columns, types, nullability, identity,
defaults, checks or indexes do not match the contract.

Review
[`sql/DB_METADATA_CONTEXT/table/DB_METADATA_CONTEXT.sql`](../sql/DB_METADATA_CONTEXT/table/DB_METADATA_CONTEXT.sql)
with your DBA. The system never auto-migrates an incompatible table.

Surplus objects also count as drift: an extra CHECK, default, index or unique constraint, a
trigger, a foreign key, or an index key whose ASC/DESC direction differs from the reviewed DDL.

## METADATA_CONTEXT_ENGINE_UNSUPPORTED

The context index is SQL Server only. PostgreSQL, MySQL, MariaDB and Oracle still export full
context files and still support Query Data. This is a stated boundary, not a failure — do not
retry it as though it were transient.

## METADATA_CONTEXT_COMPLETE_SCOPE_REQUIRED / INVENTORY_MISMATCH

Drop `--complete-catalog-id`. Complete reconciliation is accepted only for an all-mode catalog
that matched the profile scope exactly, had no filters or exclusions, analyzed everything
successfully, and whose submitted identities match its inventory.

A partial sync deactivates nothing, so it is the safe default.

## WRITE_SCOPE_REQUIRED

Enable only the scope you need:

```bash
sqlctx profile write-scope --profile <name> --metadata-context-write
sqlctx profile write-scope --profile <name> --metadata-context-write --routine-write
```

This is a one-time opt-in. Inventory sync then runs unattended; owner-mode writes still require
an approval.

## APPROVAL_REQUIRED / APPROVAL_EXPIRED

```bash
sqlctx approvals list
sqlctx approvals grant --challenge <id>
```

Then retry the **exact** request before it expires. Changing the plan ID, payload, caller or
hashes will not consume the grant.

## ROUTINE_APPLY_ENGINE_UNSUPPORTED

Routine apply is SQL Server only, with no DROP-and-recreate fallback. Other engines still
export, classify and index normally.

## A SQL Server procedure still says CREATE PROCEDURE

Create a new export or folder plan. The writer rewrites only the declaration at the start of the
definition to `CREATE OR ALTER PROCEDURE`; it never does a global body replace.

A banner comment above the declaration is fine. If the declaration is not in a supported shape
the run fails with `PROCEDURE_DEFINITION_HEADER_UNSUPPORTED` rather than guessing.

## Query Data rejects a query that used to work

2.1.0 rejects `FOR XML` and `FOR JSON`, table and query hints (`TABLOCKX`, `UPDLOCK`,
`HOLDLOCK`, `NOLOCK`, `INDEX(...)`, `OPTION (...)`), and bare identity functions such as
`CURRENT_USER`.

Select the columns directly instead of shaping the result server-side; row limits and masking
then apply normally. See the [2.1.0 notes](releases/2.1.0.md).

## A query result is truncated

`truncated` with `truncation_reason` of `row_limit` or `output_limit` means you received a
bounded sample, not the answer. Narrow the query, or use the owner CLI for all-row output. Never
read a truncated result as a total.

## The docs show a command that `sqlctx --help` does not have

You are running an older owner package even though the plugin was refreshed. Check the
executable's path and version from the same terminal, then complete the lifecycle update across
package, service and bridge, and open a new session.

Do not work around this by pointing `PYTHONPATH` at a checkout; that leaves the plugin and
runtime at different revisions.
