# Architecture

```text
Codex        Claude Code        Antigravity        Gemini CLI
  │               │                  │                 │
  └───────────────┴──────────────────┴─────────────────┘
                  │  Skill + MCP stdio bridge
                  │  (opaque IDs, safe metadata, one active profile per session)
                  ▼
        one loopback service per machine
        ServiceFacade ── protected runtime · approvals · audit
                  │
                  ├── read adapters ──── catalog, DDL, dependencies, masked samples
                  ├── managed folders ── scan → immutable plan → atomic apply
                  ├── context index ──── [agrimap_app].[DB_METADATA_CONTEXT]
                  └── routine deploy ─── immutable plan → approved SQL Server apply
```

## One service, many sessions

This is the point most easily misread: there is **one** service process per machine, not one per
harness session. Each session starts a thin stdio bridge that proxies to that shared service
over authenticated loopback HTTP.

The bridge holds almost nothing — just the active profile for its own session, so two windows
cannot inherit each other's database. Everything with real state (profiles, jobs, adapters,
approvals, audit) lives in the shared service.

Practically: when tools go missing or a session misbehaves, repair the **service** first. Never
start a second server inside a session; it will contend for the same loopback port.

The service is supervised by whatever the host actually provides — the Windows SCM, a
`systemd --user` unit, a launchd agent, or a portable pidfile supervisor — selected at install
time. See [the service knowledge base](knowledge/mcp-service.md).

## Read pipeline

The profile is the authority for engine, schemas, object types and exclusions. Discovery
collects every `TABLE`, `PROCEDURE` and `FUNCTION` inside that boundary *before* classification
runs, so `selection.mode=all` never narrows scope.

Objects that cannot be classified are still materialized, under `unknowns/`. The export writer
sanitizes, normalizes, formats, adds the managed header, hashes and bundles — and never sends a
ZIP or an absolute path through MCP.

Analysis failures are reported per object in `failures[]` with a stable error code and a
sanitized message. A failed object is never silently dropped and never counted as exported.

## Folder pipeline

The owner registers exact input and output roots from their own terminal and receives an opaque
folder ID; the agent only ever uses that ID.

The scanner accepts `.sql` only and rejects traversal, symlink and reparse-point escapes,
duplicate identities, multi-object files and collisions. A plan binds source and output hashes.
Apply stages under the OS temp directory and writes managed files and their manifest atomically.
By default it does not touch the input tree at all.

## Context index

One table, `[agrimap_app].[DB_METADATA_CONTEXT]`, holds one row per canonical identity
(`schema` + `object_type` + `object_name`). Tags and evidence are JSON arrays in that same table.
The application contract, the managed header and the database row share one validation path.

Owner-confirmed metadata always outranks a rule suggestion.

Every export ends with an `inventory` sync covering every discovered object, including ones that
could not be classified — those are recorded as `unresolved` with no guessed context. Inventory
sync needs no approval and can neither deactivate a row nor overwrite an owner classification,
which is what lets it run unattended and make the next run better informed.

Resolving an unknown is file-first: build an immutable plan from the current managed file, apply
the owner-confirmed path and header while preserving the body hash, then sync that same plan into
the index. That ordering is what prevents database-only metadata drift.

Complete reconciliation — the only mode that may soft-deactivate missing rows — is accepted only
when a retained all-mode catalog proves the exact unexcluded profile scope with zero analysis
failures and a matching identity inventory. Partial sync never changes another row's active
state.

Before any read or write the adapter verifies the table's full signature: columns, types,
nullability, identity, defaults, checks and indexes. It rejects surplus constraints or indexes,
disabled or untrusted objects, identity/computed/sparse drift, index direction drift, and any
trigger or foreign key bound to the table. It never auto-migrates.

This index is SQL Server only. Other engines return `METADATA_CONTEXT_ENGINE_UNSUPPORTED`, which
is a reported boundary, not a failure — they still export full context files.

## Write pipeline

`metadata_context_write` and `routine_write` are separate scopes, both false by default.

Supervised mutations require an idempotency key, a bound plan, a request-bound owner approval and
a sanitized audit record. Inventory-mode index writes are the one exception: they carry only
machine-derived fields and cannot overwrite owner values, so they need no per-run approval.

Only the SQL Server adapter has a reviewed routine writer. The others have no DROP-and-recreate
fallback and return a stable unsupported error instead.
