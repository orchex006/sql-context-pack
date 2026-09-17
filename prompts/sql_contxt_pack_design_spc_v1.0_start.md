# SQL Context Pack — Requirement v1.0

Status: Approved for implementation by owner on 2026-09-17.
Product/package/Skill version: 2.1.0. Output format: 2.

This is the baseline requirement. The requirement history that ran from v1.1 to v1.27 during
internal development was reset at product 2.1.0, the first released version; that development
record is summarised by date in [CHANGELOG.md](../../CHANGELOG.md). Every requirement version
after this one is additive over v1.0 and tracks a product version of 2.1.0 or higher.

## Revision v1.0 — Released baseline

1. Query Data is read-only relational SELECT, validated by parsing rather than pattern matching.
   Writes, DDL, stacked statements, comments, cross-database references, non-allowlisted
   functions, row-collapsing result clauses (`FOR XML`, `FOR JSON`), and table or query hints
   (`TABLOCKX`, `UPDLOCK`, `HOLDLOCK`, `NOLOCK`, `INDEX(...)`, `OPTION (...)`) are all rejected
   before execution. Result values are masked per column with lineage-aware redaction, and
   ambiguous lineage fails closed.
2. `[agrimap_app].[DB_METADATA_CONTEXT]` is synchronised on every export in `inventory` mode,
   covering every discovered object. Objects that cannot be classified are recorded as
   `unresolved` with no guessed context rather than omitted. Inventory mode requires no
   per-run approval and can neither deactivate a row nor overwrite an `OWNER` classification.
   Supervised `owner` writes and complete-catalog deactivation remain approval-gated.
3. Unresolved objects are presented to the owner as one consolidated supervised list carrying a
   suggested context and its evidence, marked as a suggestion. The agent never promotes a
   suggestion to a confirmed context on its own.
4. The database context index and safe routine deployment are SQL Server features. PostgreSQL,
   MySQL, MariaDB and Oracle export full context files and support Query Data, and report
   `METADATA_CONTEXT_ENGINE_UNSUPPORTED` or `ROUTINE_APPLY_ENGINE_UNSUPPORTED` as a stated
   boundary rather than an internal error.
5. Supported hosts are `codex`, `claude`, `gemini` and `agy` (Antigravity). Antigravity has no
   plugin or extension CLI, so it installs by file placement plus an additive merge into its
   shared MCP configuration, which must preserve other servers on install, update and removal.
6. Exactly one service instance runs per machine. Every harness session reaches it through its
   own stdio bridge holding only that session's active profile. The service is supervised by the
   Windows SCM, `systemd --user`, launchd, or a portable pidfile supervisor, selected by host,
   and is manageable from an installed package.
7. Updates may be pinned to an exact released tag. A version that does not exist is a stop that
   reports the available tags, never a silent fall back to the latest release.
8. Every bound is reported rather than hidden. Truncated results, paging cursors, per-call entry
   limits, security-skipped objects and analysis failures are stated with their counts; a bounded
   sample is never presented as a complete set.

The specification that follows is the authoritative product definition.

---

# SQL Context Pack — Architecture, Security, Skill, and Implementation Prompt Specification

**Status:** Approved — baseline for product 2.1.0  
**Specification version:** `1.0`
**Current product version:** `2.1.0`
**Recommended GitHub repository:** `sql-context-pack`  
**CLI command:** `sqlctx`  
**Service process:** `sqlctx-server`  
**MCP server name:** `SQL Context Pack`  
**Agent Skill name:** `sql-context-pack`  
**Export bundle extension:** `.sqlctx.zip`

### Revision v1.25 — Complete Capture, Database Context Index, and Safe Routine Deployment

This revision preserves every v1.24 requirement and adds the following owner-approved complete-
capture, classification, persisted-index, generation, and database-write behavior. Product version
becomes `1.3.0` and output format becomes `2` because managed SQL files gain a versioned context
header and unresolved-object materialization paths.

1. An all-mode catalog/export request materializes every successfully extracted TABLE, PROCEDURE,
   and FUNCTION inside the explicit owner profile schema, object-type, exclusion, and privilege
   boundary. It never widens a legacy profile or exports every table row.
2. Unresolved or ambiguous classification no longer blocks all-mode export. Confirmed objects use
   `<context>/<tables|store_procedures|functions>/`; unresolved objects use the complete equivalent
   under `unknowns/`. Extraction or security failures remain explicit and are never counted as
   successful materialization.
3. Every managed SQL file begins with a dialect-safe, versioned SQL-comment header containing
   canonical object identity, one context, optional description, normalized tags, classification
   status/source, source/content hashes, and output/header versions. Removing the header produces
   the unchanged normalized SQL body.
4. Unknown values remain null or empty. Model suggestions may be stored only as non-authoritative
   evidence; they never populate confirmed context, description, or tags. Owner resolutions and
   deterministic configured rules are the only confirmation sources.
5. SQL Server Stored Procedure definitions are normalized before header insertion. When a managed
   header is read back or deployed, the first executable declaration after it must be exactly
   `CREATE OR ALTER PROCEDURE`; global body replacement is forbidden.
6. The owner may register exact input/output folder roots, scan supported `.sql` files, preview a
   deterministic classification plan, and apply it to a separate output root by default. MCP/API
   receive opaque folder IDs and safe relative paths rather than arbitrary absolute paths.
7. Folder scans reject traversal, symlink/reparse-point escape, output-inside-input recursion,
   unsafe names, multi-object files, duplicate identities, and destination collisions. Apply
   stages under OS temporary storage, verifies unchanged content hashes, atomically updates only
   managed files, and preserves unmanaged files.
8. In-place folder reorganization is a distinct explicit, approval-bound plan. It never happens
   by inference from a scan or output path.
9. The target AgriMap SQL Server database stores the authoritative persistent object-context index
   in exactly one table: `[agrimap_app].[DB_METADATA_CONTEXT]`. No tag child table, trigger,
   stored-procedure wrapper, seed row, foreign key, or message entry is added.
10. One active row represents one canonical `(schema, object_type, object_name)` identity where
    object type is TABLE, PROCEDURE, or FUNCTION. It stores one safe primary `CONTEXT_CODE`, a
    nullable `DESCRIPTION`, and `TAGS_JSON` as a validated JSON array so values such as `um`,
    `content`, `app_state`, `dd`, and `share` are supported without a hard-coded category enum.
11. The table also stores classification status/source, source/content fingerprints, managed
    relative path, evidence JSON, header/output versions, last-classified/generated timestamps,
    and AgriMap lifecycle/audit columns. Every metadata write requires an explicit numeric owner
    actor ID; caller names and OS/harness identity are never converted into database IDs.
12. The DDL is rerunnable, creates schema `[agrimap_app]` with owner `[dbo]` only when absent, uses
    named constraints/defaults/indexes, Thai `MS_Description` properties, JSON checks, active-
    identity uniqueness, and context/status/type lookup indexes. An incompatible existing table is
    reported as schema drift and is never altered implicitly.
13. Catalog-to-index synchronization is an explicit write-scope, idempotent, request-bound owner-
    approved operation. It never overwrites owner-confirmed values with suggestions and marks
    missing objects inactive only when a complete matching profile scope proves deletion.
14. Index listing is paginated and filters by context, tag, object type, status, and active state
    without returning SQL bodies, samples, credentials, or absolute paths. Resolution validates
    context/tags, stores the supplied description exactly, and preserves safe audit evidence.
15. Index-driven generation uses confirmed rows by default. Explicit unresolved inclusion retains
    `unknowns/` paths. Source/index/header/file hash drift stops affected generation with a stable
    error instead of producing stale context.
16. Routine deployment is plan-first and accepts either one registered managed SQL file or every
    eligible file below one registered folder. Each file must contain exactly one PROCEDURE or
    FUNCTION whose header, body, path, and canonical identity agree.
17. A routine plan binds the profile, engine, ordered file set, identities, content hashes, current
    database fingerprints where readable, planned statements, requester, and expiry. Changed
    input or preconditions invalidate the plan.
18. Apply requires an explicitly routine-write-enabled profile, an unconsumed request-bound owner
    approval, unchanged plan/content/database preconditions, and a database adapter capability
    proven safe. Agent-scoped credentials alone cannot mutate the database.
19. SQL Server is the first enabled routine-write engine. Procedure apply emits exactly
    `CREATE OR ALTER PROCEDURE`; Function apply is enabled only after its create-or-alter form and
    identity/body preservation are proven. Other engines return
    `ROUTINE_APPLY_ENGINE_UNSUPPORTED` and never fall back to DROP/recreate.
20. Routine apply rejects TABLE DDL, unrelated statements, unregistered paths, duplicate targets,
    and guessed identities. It records safe per-object outcomes and audit metadata without storing
    routine bodies or secrets in MCP responses or audit logs.
21. Existing read-only query, masking, bounded sample, profile scope, catalog retention,
    idempotency, cancellation, and secret-safety behavior remains in force. Database metadata and
    routine writes are separate explicit capabilities and default off.
22. CLI, HTTP, MCP, bridge, canonical Skill, generated OpenAPI/tool schemas, harness manifests,
    documentation, tool counts, product `1.3.0`, and output format `2` remain synchronized. Large
    SQL bodies stay out of Agent transcripts and tools use folder/plan IDs and hashes.
23. The reduced documentation rewrite approved in v1.24 is completed against v1.25 current
    behavior. It covers all three harness lifecycles, complete capture, headers/unknowns, database
    context indexing, generation, routine planning/apply, troubleshooting, security, and exactly
    three progressive examples.
24. Required verification includes regression-first unit/integration/contract/e2e coverage, SQL
    artifact formatting/validation, generated-contract and provider-manifest validation,
    `scripts/dev-check.ps1 -Task all`, zero prohibited repository residue, and separate regulated
    full QA. Verification never connects to or mutates an owner database or installed runtime.

### Revision v1.24 — Clean Documentation System and Canonical SQL Server Procedure Header

This revision preserves every v1.23 requirement and adds the following owner-approved documentation
and SQL Server materialization behavior. Product version remains `1.2.0` and output format remains
`1`; existing HTTP/MCP/CLI request schemas and non-SQL Server routine behavior do not change.

1. The legacy maintained Markdown documentation under `docs/` is replaced from a clean sheet using
   current code, CLI help, manifests, canonical Skill behavior, generated contracts, tests, and
   current harness behavior as sources of truth. Previous prose is not carried forward by default.
2. Existing immutable requirements and hashes under `docs/spec/` and generated contracts under
   `docs/generated/` are protected. Previous versions remain byte-identical and generated JSON is
   never hand-edited.
3. The maintained documentation set is deliberately reduced to one documentation map, one
   zero-to-first-result guide, one lifecycle guide, one progressive-example guide, focused operator
   and technical references, and provider-specific Codex/Claude Code/Gemini CLI pages.
4. Installation, first setup, discovery verification, repair, update/upgrade, uninstall, removal
   order, preserved-data behavior, and restart/new-session requirements are complete for Codex,
   Claude Code, and Gemini CLI.
5. Provider invocation syntax is exact: Codex uses `$sql-context-pack`; Claude Code uses the plugin
   Skill namespace `/sql-context-pack:sql-context-pack`; Gemini CLI verifies discovery with
   `/skills list` and activates the Skill through an explicit natural-language request. One
   provider's syntax is never presented as universal.
6. Every executable documentation block is labeled as native terminal, Agent chat, owner terminal,
   or development checkout. Normal onboarding never requires a checkout, raw bearer token, manual
   MCP/server startup, source path, `install.ps1`, or `sqlctx launch` fallback.
7. `docs/usage-examples.md` contains exactly three progressive end-to-end examples named Simple,
   Intermediate, and Advanced. They cover bounded masked Markdown Query Data, exact named-object context
   export/validation, and adaptive ETL scope plus complete context, sync-data, and JOIN/full-or-
   bounded querying.
8. Documentation verification covers the exact maintained inventory, every local link, provider
   syntax, command execution surface, all lifecycle stages, exactly three examples, current
   version/tool counts, protected trees, and absence of obsolete maintained-page references.
9. Current tool/version counts are derived from repository metadata and generated contracts. A
   healthy service with an older MCP inventory is reported as incomplete layer synchronization,
   not successful onboarding.
10. For SQL Server only, every Stored Procedure definition read or materialized from a leading
    `CREATE PROCEDURE`, `CREATE PROC`, `ALTER PROCEDURE`, or `ALTER PROC` declaration is normalized
    case-insensitively to the exact canonical declaration `CREATE OR ALTER PROCEDURE` before
    masking, SQLFluff formatting, caching, and export.
11. Already-canonical SQL Server procedure definitions remain canonical. The normalizer preserves
    leading permitted whitespace/BOM, the object name, parameters, options, `AS`, comments after
    the declaration, and the complete procedure body. It never performs a global replacement.
12. A SQL Server procedure definition whose leading executable declaration cannot be recognized
    fails the affected object with a sanitized stable error instead of exporting guessed or
    non-canonical SQL.
13. MySQL, MariaDB, PostgreSQL, Oracle, stored-function, table, masking, formatting, classification,
    sync, and export behavior remains unchanged except for existing pipeline behavior.
14. Regression tests prove create-only, alter-only, abbreviated `PROC`, mixed-case, leading
    whitespace/BOM, already-canonical, unsupported-header, body-preservation, and non-SQL Server
    cases. Documentation and the canonical Skill state the SQL Server canonical-header contract.
15. `CHANGELOG.md`, implementation state, full residue-free development verification, and
    regulated QA are required. Live database access, installed-runtime mutation, deployment,
    commit, publish, and release remain outside this execution.

### Revision v1.23 — Consolidated Thai Working Guide

This revision preserves every v1.22 requirement and adds the following owner-approved
documentation behavior. Product version remains `1.2.0` and output format remains `1`; no runtime
or public contract changes are introduced.

1. The repository provides one consolidated Thai working guide at `docs/working-guide.md` as the
   primary operator entry point for SQL Context Pack.
2. The guide distinguishes three independent workflows: complete context creation/export,
   retained-scope data/cache synchronization, and interactive read-only Query Data.
3. Complete all-mode context means every permitted object in the requested schema/object-type
   scope and always uses empty include patterns. It never inherits an old UM/content/name subset.
4. When ETL could mean an allowed schema, an `ETL_` object-name prefix, or final category `etl`, the
   guide requires complete safe inventory inspection and one consolidated owner clarification
   before narrowing scope.
5. The synchronization guide states that `sqlctx sync-data` refreshes existing retained request
   scope, definition checkpoints, table samples, and complete LUT rows. It cannot recover objects
   omitted by an old filter and never rewrites prior exports or assembled output.
6. LUT refresh semantics are explicit: a complete retained LUT of 10 rows followed by five readable
   inserts produces a new complete 15-row snapshot rather than a stale reuse or merge.
7. Query examples include JOIN and copy-ready Markdown plus the exact default of 100 rows and
   `value_mode=short`, bounded `--max-rows 1..500`, masked `--value-mode full`, and mutually exclusive
   CLI-only `--all-rows` streaming.
8. The guide never implies that `full` disables masking, that binary is expanded, or that HTTP/MCP
   accepts unlimited results. MCP/HTTP remain bounded and owner CLI is the only all-row surface.
9. The guide documents profile selection, expected output, error/troubleshooting choices, safety
   invariants, and the installation/update requirement to open a new room/session before discovering
   changed MCP/Skill content.
10. README, getting-started, command reference, and the canonical Skill link or route users to the
    consolidated guide without duplicating contradictory behavior.
11. Documentation tests verify that the guide exists and contains the authoritative commands and
    boundaries. Requirement integrity tests prove v1.23 preserves v1.22 in full.
12. Development verification, changelog, zero-residue inspection, and regulated QA are required.
    Live database access, runtime update, restart, deployment, commit, publish, and release remain
    outside this documentation-only task.

### Revision v1.22 — Isolated Read-only Relational Query Data as Markdown

This revision preserves every v1.21 requirement and adds the following owner-approved Query Data
behavior. Product version remains `1.2.0` and output format remains `1` because this is an additive
interactive surface and does not alter managed export artifacts.

1. Owner CLI exposes `sqlctx query SQL`; HTTP exposes `POST /api/v1/query`; MCP exposes exactly one
   new core tool, `sqlctx_query_data`. Existing 24 core tools, four session-profile operations, and
   two MCP resources retain their schemas and behavior.
2. Query Data is isolated from catalog classification, materialization, export, sync, and cache
   workflows. It may share protected profiles, adapter connection/read-only primitives, strict
   masking classification, bounded-value rules, sanitized errors, and audit transport only.
3. The accepted language is exactly one read-only relational SELECT or WITH...SELECT compound and
   supports aliases, INNER/LEFT/RIGHT/FULL/CROSS JOIN, derived and correlated subqueries, EXISTS/IN,
   DISTINCT, GROUP BY/HAVING, aggregate/window expressions, ORDER BY, and set operations.
4. Parsing uses the selected adapter dialect before opening a user-query connection. Parse gaps,
   multiple statements, comments, DML, DDL, MERGE, SELECT INTO, EXEC/CALL, dynamic/external access,
   temp objects, session/transaction changes, write locks, unsafe hints, and unknown functions fail
   closed with sanitized stable errors.
5. Every base table across JOIN, CTE, subquery, and set branches must resolve through live adapter
   discovery and the protected profile schema/object/exclusion policy. CTE/derived aliases are not
   base tables; cross-database names are rejected; unqualified names must resolve uniquely.
6. Real table references are replaced by adapter-quoted discovered schema/table names. Data literals
   are replaced by driver parameters in source order; validated structural numeric literals remain
   structural. The submitted SQL text is never passed verbatim to cursor execution.
7. Query execution applies engine read-only setup. SQL Server additionally fails closed when
   effective database or referenced-object write/administrative permissions are present or cannot
   be established. Every path rolls back and closes cursor/connection in `finally`.
8. Default output is 100 rows with configurable `max_rows` 1–500. Bounded execution reads one extra
   row to report truncation and never cuts a Markdown row or cell silently.
9. Owner CLI accepts mutually exclusive `--max-rows N` and `--all-rows`. `--all-rows` has no sqlctx
   hard row-count cap and incrementally fetches/renders rows without `fetchall` or whole-result
   accumulation; timeout, cancellation, masking, 50-column limit, and host/pipe resources remain.
10. HTTP and MCP deliberately do not accept unlimited output. They remain bounded to 500 rows and
    256 KiB Markdown; callers use narrower/paged SQL or owner CLI for larger results.
11. `value_mode` is `short|full` and defaults to `short` on CLI, HTTP, and MCP. CLI exposes
    `--value-mode short|full`.
12. Short mode preserves the context rule: binary is `[BINARY N BYTES]`; payload-like column names,
    JSON/large text, and values over 200 characters use established JSON/long-text byte markers.
    Name matching is case-insensitive and therefore covers `payload`, `config_payload`, and
    `config_query_payload` without a three-name special case.
13. Full mode removes textual payload/length placeholders and emits the complete post-masking,
    Markdown-escaped text. It never disables masking and never expands binary/unsafe LOB values.
14. A bounded full-mode result that cannot fit returns `QUERY_RESULT_TOO_LARGE`; it never labels a
    truncated cell as full. Short-mode byte/row truncation is explicit in structured metadata.
15. Masking is ephemeral per query/stream. Repeated sensitive values alias consistently within that
    execution, but SQL, parameters, result values, keys, registries, and stream state are never
    cached or persisted.
16. Markdown escapes backslash, pipe, CR/LF, and control characters; null is `NULL`; duplicate output
    labels receive deterministic display suffixes; empty results retain headers.
17. Bounded structured responses contain only safe profile, ordered columns, returned row count,
    truncation flag/reason, `masked=true`, `value_mode`, and Markdown. They never contain raw rows
    outside Markdown, SQL, parameters, credentials, grants, connection data, or driver diagnostics.
18. The session bridge makes profile optional only for the new query tool, injects the active
    profile, and rejects explicit conflicts. Existing create-catalog rewriting and all profile
    lifecycle routes remain unchanged.
19. Query-specific dependency or invocation failure affects only the new tool and cannot prevent MCP
    initialization, old tool/resource listing, old-tool invocation, or preserved session state.
20. CLI stdout contains Markdown data only. A partial all-row failure exits nonzero and writes a
    sanitized diagnostic to stderr without claiming completion.
21. Query audit records only safe operation/outcome/duration/mode/count/truncation/error metadata and
    never SQL or values.
22. Existing context/export bounded-value behavior remains byte-for-byte compatible if its pure
    helper is shared with Query Data.
23. Generated HTTP/MCP contracts add only the query endpoint/tool. Product version, output format,
    profile policy, catalog/export/sync contracts, retention, and cache behavior remain unchanged.
24. Deployment, service restart, live database execution, commit, publish, and release are outside
    this implementation. A later installed MCP update requires a new room/session for discovery.
25. Acceptance includes JOIN/CTE/subquery/set success, prohibited SQL rejection, complete table
    resolution and literal binding, strict masking, exact short/full behavior, constant-memory
    all-row streaming, bounded transport failures, frozen MCP comparison, full development checks,
    changelog, regulated QA, and zero repository cache/build residue.

### Revision v1.21 — Complete All-Mode Scope and Full LUT Synchronization

This revision preserves every v1.20 requirement and adds the following owner-approved completeness
and clarification behavior. Product version remains `1.2.0` and output format remains `1` because
the correction prevents silent omission without changing existing managed file meanings.

1. `selection.mode=all` means every profile-allowed object in the explicitly requested schema and
   object-type scope. It cannot be combined with non-empty `include_patterns`; the server rejects
   that contradiction before database discovery, quota changes, or retained-state creation with a
   stable sanitized error code.
2. Explicit `exclude_patterns` and protected profile exclusions remain authoritative security and
   owner-policy boundaries. This correction never re-adds system or owner-excluded objects.
3. The canonical Skill sends an empty include-pattern list for all mode and never infers UM/Login
   or another business-name filter from an all request.
4. When “ETL” could materially mean schema `agrimap_etl`, object-name prefix `ETL_`, or final
   category `etl`, the Agent obtains the complete safe inventory first and asks one consolidated
   owner question. It must not guess a narrower scope or reuse prior UM/content filters.
5. All-mode materialization planning marks every analyzed object as included even when final
   classification is unresolved. Such objects remain visibly unresolved and are never counted as
   intentionally excluded.
6. Because managed SQL paths require a final category, export creation stops before queueing when
   an all-mode plan contains unresolved objects. The sanitized error exposes only a safe count and
   safe object IDs so the Skill can ask the owner for classification and retry.
7. The Agent consolidates unresolved all-mode items into one owner question and never invents a
   fallback category, silently omits them, or changes all mode into selected mode.
8. `sqlctx sync-data` remains same-context refresh and does not widen old retained requests. A
   catalog previously narrowed by include patterns requires a new unfiltered catalog to recover
   missing ETL objects.
9. Sync refreshes every table sample independently of unchanged-definition reuse. A table finally
   classified as `lut` must use complete deterministic pagination and strict masking in the new
   snapshot rather than reuse the prior complete row set.
10. A successful LUT refresh reflects all currently readable rows. For example, a retained complete
    10-row LUT followed by five inserted rows produces a new snapshot with 15 masked rows,
    `actual_count=15`, `all_rows=true`, and `complete=true`.
11. Per-object definition validators do not claim row-data freshness. LUT row completeness comes
    from reading all current rows during sync, regardless of an unchanged table definition.
12. Normal selected/ask semantics, masking, read-only database access, retention, quotas,
    cancellation, sync locking, cache identity, export paths, and output format remain unchanged.
13. Acceptance includes conflict rejection before discovery/state mutation, complete all-mode plan
    accounting, actionable unresolved preflight, canonical Skill clarification, complete ETL scope,
    LUT 10-to-15 sync replacement, full development verification, and zero repository residue.

### Revision v1.20 — Owner-Initiated Data and Catalog Cache Synchronization

This revision preserves every v1.19 requirement and adds the following owner-approved cache
refresh behavior. Product version remains `1.2.0` and output format remains `1` because the new
CLI command is backward-compatible and does not change export artifact paths or meanings.

1. The owner CLI exposes `sqlctx sync-data`. The command uses configured protected profiles and
   never accepts or prints database credentials, connection strings, raw SQL, sample values, or
   unrestricted runtime paths.
2. With no filters, the command refreshes the newest eligible retained catalog for every unique
   session-cache-key/request-fingerprint context. Repeatable `--profile NAME` options restrict the
   operation to named configured profiles without changing the retained request scope.
3. An eligible source is unexpired, terminal, has a session cache key and completed selection, and
   is not queued, running, cancelled, failed, corrupt, or awaiting owner selection. Newest eligible
   records are deduplicated per session/request identity; ineligible records remain unchanged and
   are reported only through sanitized reason counts.
4. Sync bypasses an exact catalog cache hit only for this explicit owner command. Normal catalog
   creation and same-session cache reuse keep their existing behavior.
5. Sync creates a new retained catalog and preserves the prior catalog until normal expiry or
   owner-authorized cleanup. A later matching normal catalog request selects the newest refreshed
   catalog as its cache hit.
6. Compatible unchanged per-object definition checkpoints may be reused. Table checkpoints never
   reuse old samples during sync: bounded table samples and complete LUT rows are read again and
   masked under the new catalog snapshot. Procedures may reuse unchanged sanitized definitions and
   dependencies under the existing validator and checkpoint rules.
7. The command recomputes live source fingerprints and returns safe counts for considered, synced,
   skipped, failed, added, changed, deleted, reused, and successfully data-refreshed objects. It
   explicitly reports when definition-change detection is incomplete for an adapter without
   per-object definition validators.
8. One profile/context failure does not discard successful independent refreshes. Context results
   contain only safe catalog/profile identity, counts, status, and sanitized error codes; raw
   exception text and database content remain protected.
9. Concurrent `sync-data` invocations against the same protected runtime are suppressed with an OS-
   released cross-process lock. A rejected duplicate returns a concise sanitized command error and
   does not mutate catalog or export state.
10. The command updates protected catalog/cache state only. It does not create exports, fetch
    bundles, assemble output, overwrite managed or unmanaged project files, or delete stale files.
11. Sync remains read-only against every database and preserves masking, schema/object allowlists,
    profile exclusions, classification, retention, quota, cancellation, audit, and error-sanitizing
    controls.
12. Acceptance covers CLI discovery and filtering, exact-cache preservation, forced row freshness,
    unchanged-definition reuse, added/changed/deleted accounting, empty eligibility, corrupt and
    failed-context isolation, cross-process duplicate suppression, secret-free JSON output, full
    development verification, and zero repository-local cache/build residue.

### Revision v1.19 — Production Resilience, Incremental Export, and Complete Table Context

This revision preserves every v1.18 requirement and adds the following owner-approved production
behavior. Product version remains `1.2.0`. Output format remains `1` because all public additions
are backward-compatible and existing managed files retain their paths and meanings.

1. Secret detection and masking remain fail-closed, but one unsafe table, routine, sample, or
   formatting unit must not fail an otherwise valid database export. The worker first performs
   deterministic redaction and a second scan. A clean redacted definition may be exported with a
   warning; a unit that remains unsafe is recorded as `skipped_security`, exposes only its safe
   object ID/name and sanitized error code, and processing continues with the next object.
2. Catalog and export checkpoints persist phase, safe object ID, source/content fingerprint,
   policy/tooling fingerprint, attempt count, status, and sanitized error code. Service startup and
   repair reconcile stale queued/running work and resume compatible completed units instead of
   leaving expired active jobs or restarting all work.
3. Final statuses and reports distinguish completed, completed-with-warnings, partial, failed, and
   cancelled work and list safe succeeded, reused, skipped-security, parse-preserved, failed, and
   retryable object counts. No failure path may expose raw SQL, samples, payloads, or credentials.
4. Every final `lut` table remains automatically materialized. LUT sample output contains every
   permitted row through deterministic cursor pagination after strict masking. Empty LUTs are valid;
   inaccessible or failed pages are reported honestly and never represented as complete.
5. Long configuration/payload values are never copied verbatim into AI-oriented samples. A valid
   JSON string in a payload-like column, JSON/native large-text column, or string longer than 200
   characters is represented as `...json string payload...(N bytes)...`; non-JSON long text uses
   `...long text payload...(N bytes)...`. `N` is the UTF-8 byte count. Adapters should project size
   metadata without transporting raw MAX payload content when the engine supports that safely.
6. Exported table context includes native description/comment when available, complete column
   metadata, primary/unique/check/foreign-key constraints, and indexes with name, uniqueness,
   primary status, ordered key columns, and included columns when supported. Generated table SQL
   and table metadata summaries must not omit these facts or duplicate constraints inconsistently.
7. Catalog creation, extraction, classification, formatting, packaging, and validation are
   observable background phases. Status exposes phase, total/processed/reused/skipped/failed counts,
   current safe object, start/heartbeat/completion timestamps, elapsed time, warning count, and a
   bounded ETA/rate when enough evidence exists.
8. The canonical Skill polls active retained work without requiring the owner to ask repeatedly. It
   reports phase/count changes at bounded intervals and rediscovery after transcript compaction.
   Owner CLI `catalog watch` and `export watch` provide the same safe progress view.
9. Catalog reuse is object-incremental. Discovery builds a source manifest and diffs added, changed,
   unchanged, and deleted objects. Unchanged sanitized definitions/classification evidence may be
   reused only when source, masking, classification, dependency, and schema-policy fingerprints
   match. Changed objects and affected relationship neighbors are refreshed; deleted objects are
   removed from the new managed manifest without deleting unmanaged owner files.
10. Definition freshness and row-data freshness are separate. SQL Server object identity and
    `modify_date` detect table/routine definition changes. LUT data is refreshed every run unless a
    stronger owner-approved data-version fingerprint is available. Ordinary bounded samples use an
    explicit TTL/fingerprint policy and never claim that object `modify_date` detects row changes.
11. Formatted SQL is content-addressed by cleaned definition hash, dialect, SQLFluff version,
    formatter configuration, host-Python/tooling fingerprint, and output format. Compatible cached
    results are reused per object; changed content is reformatted independently.
12. Extraction removes repeated metadata queries, reuses table columns/constraints within one
    object, uses a small bounded connection pool/concurrency policy, and preserves adapter-specific
    cancellation and read-only behavior. It must not open a fresh database connection for every
    metadata subquery when pooling is supported.
13. SQLFluff execution preserves per-file failure isolation while avoiding three fresh interpreter
    processes per unchanged object. A bounded persistent worker or equivalent batch execution may
    be used, followed by deterministic verification and rollback for the affected file only.
14. Install/setup/repair output records stage durations and layer cache decisions. First install
    resolves/downloads dependency artifacts once per host-Python ABI and reuses the OS-temporary
    wheel set for owner CLI/bridge and platform service staging. Identical repair remains a no-op.
15. `doctor --mcp` verifies native plugin registration, launcher resolution, bridge initialize,
    core/session tool discovery, authenticated upstream health, and current provider compatibility.
    `repair --component auto|package|service|mcp` repairs only failed layers and clearly states when
    a new room/session is required. Service health alone is not sufficient MCP repair evidence.
16. Plain Codex plugin discovery plus the session bridge is the normal Codex path. `Auth Unsupported`
    on a STDIO registration is explained as a transport label, not treated as success or failure.
    `sqlctx harness run --harness codex` remains an explicit diagnostic/compatibility fallback.
17. Acceptance includes secret-containing routine and row cases, skip/continue and resume, full LUT
    rows, JSON and non-JSON long payload placeholders, table descriptions/indexes/constraints,
    stale-job recovery, object-level cache diff, changed/deleted objects, sample-data freshness,
    progress/watch behavior, MCP end-to-end doctor/repair, install stage timing, formatter cache,
    bounded concurrency, and a representative cold/warm performance benchmark.

### Revision v1.18 — Cross-Platform Frozen Requirement Hash

This revision preserves every v1.17 requirement and makes frozen raw-requirement integrity
verification independent of Git working-tree line-ending conversion. Product version and output
format remain `1.2.0` and `1` because runtime behavior and public artifacts are unchanged.

1. The canonical SHA-256 for `prompts/requiremenr.raw.prompt.md` must represent its Git-normalized
   LF byte form.
2. Integrity verification must normalize CRLF pairs to LF before hashing so Windows and Linux
   runners validate the same immutable textual content.
3. Any content change other than the platform line-ending representation must continue to fail
   the frozen raw-requirement hash contract.

### Revision v1.17 — Residue-Free Cross-Platform CI Verification

This revision preserves every v1.16 requirement and makes CI verification obey the existing
repository-residue policy on case-sensitive Linux runners. Product version and output format remain
`1.2.0` and `1` because no runtime API or export artifact changes.

1. The tracked changelog filename must be exactly `CHANGELOG.md` on case-sensitive filesystems.
2. CI must disable Python bytecode output and remove repository-local build, egg-info, and tool
   cache residue created by development dependency installation before contract tests execute.
3. CI pytest commands must disable the repository-local cache provider and place temporary test
   payloads under the runner's OS temporary directory.
4. CI format, lint, typecheck, cleanup, and build verification must use `scripts/dev-check.ps1`
   so cleanup occurs in `finally`, including when a command fails.
5. `scripts/dev-check.ps1` must expose a cleanup-only task for sanitizing a checkout after package
   installation without running an unrelated verification phase.

### Revision v1.16 — Platform-Scoped Bootstrap Removal

This revision preserves every v1.15 requirement and narrows the cross-platform bootstrap removal
contract. Product version and output format remain `1.2.0` and `1` because runtime APIs and export
artifacts are unchanged.

1. `scripts/bootstrap.py --operation remove` remains supported on Linux, macOS, and Unix for their
   owner-local platform runtime removal path.
2. Windows must reject `scripts/bootstrap.py --operation remove` before invoking any installer,
   updater, service operation, package operation, or native harness operation.
3. The Windows rejection must direct the owner to `$sql-context-pack uninstall`, which selects the
   current provider and uses `scripts/lifecycle.ps1 -Operation uninstall -Harness <provider>`.
4. Existing Windows setup, repair, update, and canonical Skill uninstall behavior remains unchanged.
5. Regression coverage must prove that Windows removal cannot invoke the bootstrap installer and
   that Linux, macOS, and Unix still receive the `remove` operation.

### Revision v1.15 — Cross-Platform Managed Runtime Lifecycle

This revision preserves every v1.14 requirement and changes the managed runtime lifecycle from
Windows-only to cross-platform. Product version and output format remain `1.2.0` and `1` because
the exported artifact format and HTTP/MCP contracts are unchanged.

1. `$sql-context-pack setup`, repair/update, and uninstall must have supported owner-local runtime
   paths on Windows, Linux, macOS, and Unix.
2. Windows continues to use the existing PowerShell/Windows Service lifecycle with owner-approved
   UAC, ProgramData staging, protected ACLs, and authenticated health verification.
3. Linux uses a `systemd --user` service when `systemctl` is available; if unavailable, it falls
   back to an owner background process with pid/state files.
4. macOS uses a user LaunchAgent through `launchctl`; if unavailable, it falls back to an owner
   background process with pid/state files.
5. Other Unix hosts use the owner background-process manager with pid/state files.
6. Cross-platform bootstrap must install or update the owner package with the selected host Python,
   verify `sqlctx`, `sqlctx-server`, and `sqlctx-mcp-bridge` launchers, start the local loopback
   runtime, and verify authenticated health.
7. Linux/macOS/Unix lifecycle must not require root, sudo, a system service, a firewall rule, a
   Python environment, or a bundled Python. It runs at owner-user scope only.
8. Documentation and OS guide output must state that managed setup is supported on Windows,
   Linux, macOS, and Unix while still noting that native harness CLI availability is provider and
   platform dependent.

### Revision v1.14 — OS Detection and Platform-Specific Install Routing

Superseded by v1.15 for managed runtime support: Linux, macOS, and Unix now have supported
owner-local setup, repair/update, and uninstall paths. The v1.14 requirements below are retained
for history, with items 4 and 5 updated to reflect the current release behavior.

This revision preserves every v1.13 requirement and adds an owner-facing OS detection guide.
Product version and output format remain `1.2.0` and `1` because this revision adds installation
routing guidance without changing runtime or export contracts.

1. The repository must provide a cross-platform OS detection command that classifies the host as
   Windows, Linux, macOS, or Unix.
2. The OS detection command must not install Python, mutate PATH, create an environment, request
   elevation, start a service, or modify files. It prints guidance only.
3. Windows output routes owners to native harness plugin/extension install, a new room/session,
   `$sql-context-pack setup`, UAC approval, bridge/service health verification, a final new
   room/session, and `$sql-context-pack connect <profile>`.
4. Linux, macOS, and Unix output must state that managed `$sql-context-pack setup`, repair/update,
   and uninstall are supported in this release through owner-local platform runtimes.
5. Linux, macOS, and Unix output routes owners to the explicit host-Python managed workflow:
   verify `python3`, install the package into the selected host interpreter user site when
   appropriate, configure/test profiles, and start the loopback service through the platform
   runtime manager.
6. Documentation must expose this guide as the recommended first command for owners unsure which
   installation path applies to their machine.

### Revision v1.13 — MCP Readiness, Profile Removal, Timeout/Retry, and All-Mode Semantics

This revision preserves every v1.12 requirement and adds the following owner-approved operating
rules. Product version and output format remain `1.2.0` and `1` because this revision tightens
Skill routing, diagnostics, and owner-local profile cleanup without changing exported file format.

1. `$sql-context-pack setup` must verify that the installed package produced the `sqlctx`,
   `sqlctx-server`, and `sqlctx-mcp-bridge` launchers. A missing bridge launcher is a setup failure,
   not a successful install.
2. The Agent must not run `sqlctx launch` as an internal fallback after setup or connect failure.
   Launching a new harness is an explicit owner terminal action only. If the current room lacks
   SQL Context Pack MCP tools after setup, the Agent reports the missing tools and tells the owner
   to open a new room/session.
3. `$sql-context-pack connect <profile>` succeeds only through the session MCP bridge tool
   `sqlctx_connect_profile`. If catalog/export tools are not exposed in the room, the Agent reports
   MCP discovery as incomplete instead of starting a separate MCP/harness process.
4. Profile removal is supported through the owner-local command
   `sqlctx profile remove <profile> --yes`. It removes the YAML profile entry and removes the
   protected credential record only when that credential reference is not used by another profile.
   `--keep-credentials` preserves the protected credential record explicitly.
5. Profile removal is not exposed as an MCP tool because it is destructive owner configuration.
   `$sql-context-pack remove-profile <profile>` routes the owner to the exact terminal command and
   never guesses a profile name.
6. Catalog/export polling may continue beyond 300 seconds while progress or heartbeat data changes.
   When loading is incomplete, the Agent must report safe failed/unloaded object IDs or names from
   status, sitemap, classification requests, export reports, or validation errors.
7. Failed explicit compatibility export batches may be retried no more than three total attempts
   with the same normalized request. After the third attempt, the Agent stops and reports the
   terminal safe error and affected objects.
8. “Create all SQL context from the active profile under ...”, “export all”, and Thai equivalents
   for “ทั้งหมด” mean `selection.mode=all`. The final materialization exports every table and
   stored procedure allowed by the active profile's schema allowlist, object-type policy, and
   exclusion patterns after full analysis.
9. `ask` mode is used only when the owner asks to choose categories or omits all/selected intent.
   The Agent must not silently reuse a previous `um`/`content` category selection for an all-mode
   request.

### Revision v1.12 — Agent and Harness Managed Lifecycle Commands

This revision preserves every v1.11 requirement and adds the following owner-approved operator
documentation rules. Product version and output format remain `1.2.0` and `1` because this revision
clarifies supported lifecycle orchestration without changing application or export contracts.

1. One canonical operator document must present the complete native harness plus Agent Skill
   lifecycle in this order: install, repair/update, Agent command list, and uninstall.
2. The canonical flow covers Codex, Claude Code, and Gemini CLI with exact native
   marketplace/extension commands followed by the canonical `$sql-context-pack` Agent action.
3. Normal marketplace users must not need a source checkout or manually invoke the product CLI,
   Python package installer, service installer, MCP launcher, or lifecycle PowerShell scripts.
4. Native install makes the Skill available; `$sql-context-pack setup` then explains and deploys
   the owner package and automatic loopback platform runtime from the installed plugin cache. The
   owner explicitly approves any UAC request and opens a new room/session at documented discovery
   boundaries.
5. Repair while the Skill remains discoverable uses `$sql-context-pack repair`. If native plugin
   discovery is missing, the operator reinstalls the provider plugin/extension first and then runs
   `$sql-context-pack setup` in a new room/session.
6. Update first uses the provider's native marketplace/extension update command, then opens a new
   room/session and runs `$sql-context-pack setup` so the exact refreshed plugin cache deploys only
   changed runtime layers. A final new room/session loads changed Skill or MCP content.
7. The Agent command list includes setup, repair, help, profiles, connect, change-profile,
   disconnect, context creation/resume, doctor, runtime status, approvals list, explicit
   certificate-trust routing, and uninstall. It distinguishes chat/Skill actions from native
   harness terminal commands.
8. Uninstall must be initiated with `$sql-context-pack uninstall` while the Skill is still
   installed. It removes the platform runtime and owner package before the dedicated native
   plugin/extension and marketplace. Direct native plugin removal alone is prohibited as the
   documented normal uninstall path.
9. Encrypted profiles, configuration, and retained runtime data remain preserved by default.
   Purging owner data requires a separate, explicit owner decision and is outside this lifecycle
   quick reference.
10. Documentation acceptance verifies the canonical guide exists, all local links resolve, all
    three harness lifecycles are present, required Agent actions are listed, and no product-CLI or
    development-checkout command appears in its executable command blocks.

+### Revision v1.11 — Lean AI Output, Resilient Export, and Default LUT Context

This revision preserves every v1.10 requirement and adds the following owner-approved export and
operability rules. Product version and output format remain `1.2.0` and `1` because the full
machine-artifact profile remains available explicitly and the lean profile is a backward-compatible
request option.

1. The default export profile is `ai`. Unless the owner explicitly requests `full`, export skips
   machine-only JSON/JSONL artifacts and their computation, including `catalog.json`, graph/index
   construction, and JSON diagnostic reports. The implementation must not build and then hide these
   artifacts.
2. The `full` profile is opt-in only. No Skill inference, omitted argument, retry, resume, or
   compatibility path may silently enable it.
3. Lean output contains materialized SQL, sample files, category YAML, `manifest.yaml`, a concise
   Markdown context index, and Markdown export/integrity/SQLFluff summaries. Protected runtime JSON
   needed for service state may remain internal but is never copied into lean output or offered for
   model reading.
4. Sample output supports `markdown`, `csv`, and `json`. Markdown is the default. JSON samples
   are produced only after an explicit request. Samples remain deterministic, strictly masked, and
   are emitted only for materialized tables.
5. The built-in `lut` business category recognizes owner-approved lookup-table naming rules.
   Materialization always includes final `lut` objects by default, even in selected-category mode,
   and records `policy_always_include` as the visible reason. This rule does not bypass schema,
   object-type, system-object, exclusion-pattern, masking, or owner-resolution policy.
6. Export status includes `created_at` consistently across retained job, HTTP, MCP, and
   idempotency replay contracts. A completed artifact must never become unreadable because a strict
   response model omitted retained job metadata.
7. Export creation returns a queued/running descriptor promptly and performs formatting, packaging,
   and hashing outside the MCP/HTTP request lifetime. Status exposes bounded progress and retained
   artifacts remain rediscoverable after client timeout or transcript compaction.
8. Omitting explicit object IDs exports the complete final materialization plan server-side. The
   Agent must not carry hundreds of object IDs through the transcript. Explicit batches remain
   bounded and stable for compatibility.
9. Owner CLI bundle fetch uses a protected local artifact fallback after service timeout or
   retriable 5xx status, validates the same declared size and hashes, and never exposes bundle bytes
   or bearer tokens to the model.
10. Sample acquisition failure is reported separately from object-analysis failure. An object whose
    sanitized definition was extracted successfully remains analyzed; a missing sample becomes a
    warning and cannot inflate discovered or failed-analysis accounting.
11. Safe status and summaries report configured/requested/actual sample counts, materialized object
    counts, output profile, sample format, generated file count/bytes, and skipped machine-artifact
    stages.
12. Multi-batch assembly accepts lean and full bundles deterministically, refuses mixed profiles,
    preserves unmanaged files, aggregates Markdown reports for lean output, and validates complete
    hash/accounting equations without requiring skipped JSON files.
13. Acceptance covers default lean export, explicit full export, all three sample formats, LUT
    inclusion in selected mode, export-status replay, timeout/local recovery, server-resolved full
    materialization, multi-batch assembly, secret scanning, and proof that the lean path never calls
    the graph/index builder.

### Revision v1.10 — Native Marketplace Lifecycle and Fingerprinted Runtime Updates

This revision preserves every v1.9 requirement and adds the following owner-approved installation
and update rules. Product version and output format remain `1.2.0` and `1` because the release is
still unreleased and the public database/export contract is backward compatible.

1. Codex and Claude install from repository marketplaces named `sql-context-pack`; Gemini installs
   the repository as an extension. The plugin/extension name is always `sql-context-pack`.
2. Native installation makes the canonical Skill available immediately. Because native plugin
   managers do not run privileged OS post-install scripts, first use offers an explicit bundled
   bootstrap. It explains access, requests UAC once, and installs the owner package and loopback
   Windows Service from the installed plugin cache without asking for a checkout/source path.
3. Native artifact management and managed-runtime lifecycle remain separate security boundaries.
   Marketplace install/update/uninstall manages the Skill/plugin copy; the bundled lifecycle script
   manages Python package and Windows Service state. Silent elevation from install hooks is
   prohibited.
4. Uninstall must remove the `SQLContextPack` Windows Service and owner Python package before the
   dedicated native plugin/extension and marketplace are removed. Encrypted profiles and retained
   runtime data are preserved by default and require a separate explicit purge decision.
5. Every install, update, repair, and first-use bootstrap uses safe layer fingerprints for source
   application, dependency lock plus database extras, selected Python executable/ABI/platform,
   plugin inventory, and service host. Credentials, tokens, connection values, samples, and runtime
   database metadata are never fingerprint inputs or install-state fields.
6. An unchanged application/dependency/plugin/service-host fingerprint with successful
   authenticated health is a no-op: no wheel build, pip install, service stop/restart, PATH rewrite,
   or new-room requirement. Git refresh with an unchanged commit therefore performs no runtime
   mutation.
7. An application-only change builds exactly one wheel in OS temp, installs the owner package with
   `--no-deps`, reuses the staged service dependency layer, replaces only application files, then
   restarts and health-checks the service. The wheel and staging data are removed in `finally`.
8. Dependency/extras or Python ABI/platform changes rebuild the complete dependency layer. Plugin-
   only changes update native discovery and require a new room but do not restart the service.
   Service-host-only changes restart only the host. Repair verifies health and replaces only the
   missing, corrupt, or fingerprint-mismatched layer.
9. Update/install output reports cache hit/miss and each changed layer. Installation state is stored
   under protected/user-owned product roots and is schema-versioned, deterministic, and free of
   secrets.
10. Release acceptance covers fresh installation, identical install, code-only update, dependency
    update, plugin-only update, service-host update, damaged-layer repair, native uninstall service
    removal, rollback, authenticated API health, and zero repository/OS-temp residue.

### Revision v1.9 — Multi-Schema Discovery, Session Cache, and Operable Approval UX

This revision preserves every v1.8 requirement and adds the following owner-approved operational
rules. Product version and output format remain `1.2.0` and `1` because this is an unreleased,
backward-compatible reliability correction.

1. A profile schema list is an explicit metadata allowlist, not a claim about every schema visible
   to the database principal. Owner discovery may list visible-but-not-allowed schemas without
   connection values. `agrimap-dev` is approved for exactly `agrimap_app`, `agrimap_etl`, and
   `agrimapadm`; it must not implicitly include other visible schemas such as `sde` or
   `agrimap_nostra`.
2. Native discovery excludes database-system objects (`is_ms_shipped = 0` on SQL Server) and then
   applies case-insensitive owner-configured object-name exclusion globs. `agrimap-dev` excludes
   `i[0-9]*`, so maintenance objects such as `i122_get_ids` are neither analyzed nor proposed as
   business categories.
3. Catalog/category reuse is scoped to one MCP/API session and expires after 24 hours. A cache hit
   requires the same normalized catalog request and an unchanged source metadata fingerprint. For
   SQL Server that fingerprint includes allowed schema, object identity/type, and `modify_date`, so
   object count, table, or stored-procedure changes invalidate the cache. Cache hits and expiry are
   visible in safe status; no cache may cross sessions or add raw samples.
4. Every MCP `APPROVAL_REQUIRED` response must preserve its safe Challenge ID, operation, target,
   expiry timestamp/countdown, exact owner grant command, and list command. The Agent reports that
   contract directly, retains the original payload, and retries it after grant; it never asks the
   owner to rediscover or transcribe an ID already returned by the service.
5. `sqlctx approvals list` shows pending, granted, consumed, and expired challenges.
   `sqlctx approvals grant` may select the sole pending challenge or offer a local interactive
   choice, while request binding, one-time use, expiry, and interactive owner presence remain
   mandatory. Automatic Agent approval is prohibited. An expired challenge instructs the Agent to
   retry the original operation once to obtain a fresh challenge.
6. `sqlctx runtime status` identifies the protected runtime root and safe counts/sizes/retention;
   `sqlctx runtime cleanup-expired` removes only eligible expired state. Catalog deletion and expiry
   remove both catalog and snapshot trees, export expiry releases dependency pins, terminal
   Challenge records are removed after a 24-hour owner-visible retention window, and temporary work
   is cleaned in `finally` blocks.
7. Windows Service/API/MCP/CLI production errors are concise, sanitized, actionable, and correlated;
   traceback data is written only to protected owner/service diagnostics. Development mode may
   show full tracebacks when `SQLCTX_DEBUG_ERRORS=1` is explicitly set. Public responses never
   expose credentials, connection strings, SQL, samples, or filesystem internals.
8. Release verification must exercise the authenticated installed HTTP API against the real
   read-only `agrimap-dev` profile, prove all three approved schemas are represented, prove
   `i[0-9]*` and SQL Server system objects are absent, prove same-session cache reuse, and prove an
   approval error contains an actionable Challenge ID. Sanitized counts and IDs may be recorded;
   database values and secrets may not.

### Revision v1.8 — Complete Marketplace Lifecycle and Observable Source Update

This revision preserves every v1.7 requirement and adds the following owner-approved documentation
and update rules. Product version and output format remain `1.2.0` and `1` because this is an
unreleased backward-compatible lifecycle correction.

1. Operator documentation must contain one discoverable Codex personal-marketplace lifecycle page
   covering complete install, plugin/package-only install, status, unified update,
   plugin/package-only update, Codex registration recovery, plugin artifact uninstall, and the
   separately scoped Windows Service removal.
2. The documentation must distinguish `codex plugin remove sql-context-pack@personal`, which removes
   only Codex registration/cache, from the project removal operation, which additionally removes
   this plugin's installed source and marketplace entry while preserving unrelated entries.
3. The default personal marketplace at `%USERPROFILE%\.agents\plugins\marketplace.json` is discovered
   implicitly. Instructions must not tell users to register that default marketplace manually or
   remove its root to uninstall one plugin.
4. `sqlctx update`, with or without `--source`, must first refresh the selected trusted Git checkout
   using a fast-forward-only pull and then install the refreshed package, plugin, MCP bridge, hook,
   and Windows Service. A non-Git or non-fast-forward source fails before installation.
5. Update output must show separate Git-source and installation/service phases so the owner can see
   whether source was downloaded. `sqlctx repair --source <checkout>` remains the explicit flow for
   deploying the exact current development tree without contacting Git.
6. Plugin-only update from a checkout must be documented as not fetching Git and not updating the
   Windows Service. Every plugin content install/update/re-registration requires a new Codex room.
7. An active Codex room locking the bridge console launcher must not abort the unified update. The
   installer preserves that running process and session, transactionally replaces importable owner
   package files without replacing the locked launcher, continues plugin/service health gates, and
   requires a new room to load the updated bridge.

### Revision v1.7 — Explicit Development TLS Trust Policy

This revision preserves every v1.6 requirement and adds the following owner-approved rules. Product
version and output format remain `1.2.0` and `1` because this is an unreleased backward-compatible
profile-policy correction.

1. `trust_server_certificate` is an explicit per-profile boolean policy, defaults to `false`, and is
   valid only for SQL Server profiles. Other engines reject the option.
2. SQL Server encryption remains mandatory. The adapter emits `Encrypt=yes` in both modes and emits
   `TrustServerCertificate=yes` only when that exact resolved profile has the owner-approved flag.
3. Interactive configuration explains the reduced certificate-chain assurance and defaults to No.
   The deterministic owner command is
   `sqlctx profile trust-certificate <profile> --enable|--disable`.
4. Safe profile descriptors may expose the boolean policy but never connection values. Changing the
   policy does not resolve, print, or rewrite encrypted host/database/user/password values.
5. The owner explicitly approved `trust_server_certificate: true` for development profile
   `agrimap-dev`. No global trust switch or implicit `-dev` name heuristic is permitted.
6. Release evidence must retest the installed authenticated API. Successful connection must proceed
   into the bounded real catalog/category-preview/export/validation slice required by v1.6.

### Revision v1.6 — Session Profile and Managed Windows Service

This revision preserves every v1.5 requirement except where the following owner-approved rules
explicitly supersede it. Product version `1.2.0` is a backward-compatible feature expansion; output
format version remains `1`.

#### Normative lifecycle decision

```text
Windows Service owns the persistent loopback HTTP/API runtime.
A plugin-bundled MCP bridge owns active profile state for exactly one Codex session.
Profile changes never restart the shared Windows Service.
Every database-starting API call still receives one explicit resolved profile.
```

1. The canonical Windows install performs Python/package preflight, stages the application under a
   stable `%ProgramData%\SQLContextPack` installation root, applies an ACL for `SYSTEM` and the
   installing owner, registers and starts the `SQLContextPack` Windows Service, installs the Codex
   plugin with bundled MCP/hook discovery, installs the exact pinned driver extras required by all
   configured profile engines, and verifies service health and MCP discovery.
2. Before elevation or mutation, the installer prints the exact stage, target, requested access,
   and reason. Loopback binding requires no firewall rule and the installer must not request one.
3. The service binds only to `127.0.0.1`. Profiles, credentials, retained jobs, and runtime state
   remain outside the replaceable application tree and survive update/rollback.
4. The Codex plugin bundles `.mcp.json` and starts a lightweight STDIO MCP bridge for each Codex
   session. The bridge reads protected service metadata locally, never emits credentials, and
   forwards operations to the persistent loopback API.
5. Active profile state belongs to the MCP bridge process, is initially empty, and is forgotten
   when that session bridge exits. A `SessionStart` hook may inject safe status/help context but
   must not own profile state, credentials, or database values.
6. `connect <profile>` runs a bounded read-only profile connection test and activates only after
   success. `change-profile <profile>` tests first and atomically replaces the prior profile;
   failure retains the prior active profile. `disconnect` clears only session profile state and
   never cancels retained catalog/export jobs.
7. When neither active nor explicit profile exists, return `409 PROFILE_NOT_CONNECTED`. When an
   explicit profile conflicts with the active profile, return `409 PROFILE_CONTEXT_CONFLICT` and
   require an explicit profile change. The same active and explicit profile is accepted.
8. Existing explicit-profile clients remain supported. The resolved profile—not an omitted/null
   field—must remain part of catalog request normalization, idempotency fingerprints, status,
   audit metadata, cache keys, and resume validation.
9. The canonical Skill supports `$sql-context-pack help`, `profiles`, `connect [profile]`,
   `disconnect`, and `change-profile [profile]`. Missing profile arguments produce an interactive
   choice from safe ready-profile descriptors. `$sql-content-pack profiles` may be recognized as a
   typo alias but must never become a second product/Skill name.
10. Interactive chat help presents discoverable choices. The owner CLI presents a TTY menu when
    invoked interactively and preserves deterministic non-interactive commands and JSON output.
11. Product update is exposed as `sqlctx update`. It is distinct from
    `sqlctx sqlfluff update`. Update uses a fast-forward-only refresh of the recorded trusted Git
    checkout or an explicit owner-provided local package/checkout, stages content, stops the service, atomically replaces
    application/plugin/service files, starts the service, runs health/MCP smoke tests, and rolls
    back on failure.
    `sqlctx repair --source <checkout>` and root `install.ps1 -Repair` must idempotently restage the
    current trusted development source without a Git refresh, preserve owner data, recreate a
    missing service, and health-check it after an interrupted install or source change.
    Health acceptance must use freshly rotated protected metadata, require the exact staged product
    version and a Running SCM service, migrate an identified legacy foreground sqlctx listener, and
    fail `PORT_IN_USE` for any unrelated listener; an old process must never produce a false pass.
    The managed LocalSystem host passes the staged Windows import/DLL paths to its API child, writes
    owner/`SYSTEM`-only child diagnostics, and removes validated stale stage/backup directories only
    after authenticated health succeeds.
12. `$sql-context-pack update` explains and routes to the owner-controlled updater; the Agent never
    grants elevation or silently updates its own loaded plugin. Current Codex rooms are not
    promised Skill hot reload. A new room is required when the active host cannot reload changed
    plugin/Skill content.
13. Installation uses a stable `sqlctx` launcher/shim. The root PowerShell installer updates the
    current process PATH when possible and persistent PATH once; routine updates do not rewrite a
    terminal profile or require repeated PATH changes.
14. Development/test/build entrypoints route bytecode, pytest, mypy, Ruff, and build staging to OS
    temporary storage or disable those caches. Cleanup runs in `finally`, removes known generated
    residue (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `build`, `dist`, and
    `*.egg-info`), and verifies none remains. `.gitignore` is defense in depth, not cleanup.
15. Release closure requires installed-artifact verification through the real Windows Service:
    health, profiles, failed-connect no-activation, connect, active status, catalog creation,
    change-profile, disconnect, two simultaneous Codex sessions, update rollback, and MCP tool
    discovery.
    Windows Service Control Manager recovery restarts transient process failures; repair/update is
    the owner-controlled recovery path for corrupt, incomplete, or changed application files.
16. At least one reachable owner-provided read-only database must complete a bounded real HTTP and
    MCP catalog/category-preview/export/validation slice. Evidence records sanitized codes, counts,
    and hashes only. If the configured database is unreachable, release verification is blocked;
    it must never be reported as passed.

#### Explicitly superseded v1.5 clauses

- The v1.5 owner-started foreground process is replaced on Windows by the installed owner-approved
  Windows Service. Manual foreground startup remains a development/diagnostic fallback.
- The v1.5 default prohibition on plugin-bundled MCP/STDIO is replaced by the session-scoped STDIO
  bridge. The database/API runtime remains the authenticated loopback Windows Service; the bridge
  is not a second database service.
- The v1.5 user-site-only installation rule remains the default for non-service/manual mode. Windows
  Service mode may stage pinned application dependencies under `%ProgramData%\SQLContextPack` while
  continuing to use the owner-selected host Python and without creating a venv, conda environment,
  pipx environment, or bundled Python.
- The v1.5 `restart_required: true` installer result is replaced by explicit fields distinguishing
  `service_restart_performed`, `current_shell_ready`, and `new_codex_room_required`.

All remaining v1.5 security, masking, read-only database, full-analysis, export, integrity,
retention, output-format, and cross-harness requirements continue unchanged.

### Revision v1.5 — Host-Python Runtime Correction

This revision retains every accepted core behavior and defines the product `1.1.0` global distribution and owner-setup release:

- one GitHub monorepo and one canonical Agent Skill shared by Codex, Claude Code, and Gemini CLI,
- vendor-specific plugin/extension manifests and connection examples without duplicating workflow logic,
- an explicit owner-started service default and an opt-in client-managed STDIO trade-off,
- a deterministic-server versus model-harness classification boundary,
- complete MCP tool input/output contract mapping and examples,
- normative interpretation of the ten-row sample requirement,
- required operator, harness, command, case-by-case, troubleshooting, and security documentation,
- SemVer, changelog, manifest-version, and release-consistency rules,
- cross-harness conformance tests and requirement traceability,
- a pinned SQLFluff materialization stage with internally consistent manifest counters,
- symmetric report access, paginated classification responses, job list/cancel/delete operations, and explicit idempotency,
- one canonical `output_format_version` field name across manifests, validation, cache keys, and prose,
- deterministic HMAC alias encoding with collision handling and resumable per-snapshot registries,
- bounded runtime retention, authenticated token handoff, and a canonical large-bundle download path,
- Python 3.11 minimum, checked-in CI, development-versus-release version rules, and fresh-session chunk execution,
- removal of optional relationship-aware sampling and dependency-closure materialization because they are outside the raw v1 requirement.
- resumable snapshots retain or deterministically derive the same protected masking key,
- production uses the Python interpreter already installed/selected on the user's machine,
- every SQLFluff command uses that same explicit host interpreter,
- the Skill/plugin/server never creates or manages a virtual environment, conda environment, or pipx environment,
- SQLFluff installs to the selected interpreter's user site only after owner approval; host policy is never bypassed,
- SQLFluff update is allowed only while no export/format job is active, then verifies and rolls back on the same interpreter,
- a non-Python preflight provides official Windows/macOS/Linux installation guidance when Python >=3.11 is unavailable,
- all category previews use the standard cursor envelope and paginated MCP traversal uses tools rather than resources,
- rediscovery requires an exact normalized-request fingerprint,
- mandatory SQLFluff and sample stages cannot be disabled by public export input,
- completed export status exposes immutable bundle integrity metadata,
- final validation re-reads and hashes the files actually assembled in the destination,
- owner-authorized operations require a separate owner credential and request-bound one-time approval,
- catalog retention is pinned by active or unexpired dependent exports,
- v1.4 established product `1.0.0`; final v1.5 uses `1.1.0`, and later corrective iterations bump patch (`1.1.1`, `1.1.2`, …) under the non-circular two-phase gate without a final version transition.

The central invariant is:

```text
User category selection controls what is materialized into the project.
It must never reduce the database objects analyzed by the server.
```

---

## 1. Naming Decision

### 1.1 Recommended name: SQL Context Pack

Use **SQL Context Pack** as the product name and `sql-context-pack` as the GitHub repository name.

The name describes the real responsibility of the system:

- Extract SQL schema definitions.
- Extract stored routine definitions.
- Attach sanitized representative sample data.
- Classify database objects into business categories.
- Build indexes and relationship metadata.
- Package the result as AI-ready context.
- Expose the capability through HTTP and MCP.
- Prepare metadata for later ER diagram and graph rendering.

Do **not** use `db-dump-skills` as the primary name. “Dump” describes only extraction and does not communicate masking, classification, indexing, relationship analysis, or AI context packaging.

### 1.2 Names to avoid

- `sql-context-forge`: avoid brand confusion with IBM ContextForge.
- `sql-dump-skill`: too narrow.
- `database-rag`: implies a retrieval system that is not required in v1.
- `schema-seed`: could be confused with database seed-data generation.
- Names containing `agrimap`: the project must remain vendor- and organization-neutral.

### 1.3 GitHub repository count and authoritative development layout

Create **one GitHub repository**, `sql-context-pack`, as a Python monorepo. Do not create separate repositories for the server, CLI, Skill, or individual harnesses in v1.

Reasons:

- HTTP, MCP, CLI, and all harness packages share one application core and one release version.
- One canonical `SKILL.md` prevents Codex, Claude Code, and Gemini CLI workflows from drifting.
- Contract tests, fixtures, security rules, and changelog updates can be atomic in one pull request.
- A split repository would add cross-repository version coordination without providing a v1 requirement benefit.

Use this structure:

```text
sql-context-pack/
├── .github/
│   └── workflows/
│       └── ci.yml
├── .codex-plugin/
│   └── plugin.json
├── .claude-plugin/
│   └── plugin.json
├── .mcp.json.example
├── gemini-extension.json
├── skills/
│   └── sql-context-pack/
│       ├── SKILL.md
│       ├── references/
│       ├── scripts/
│       └── examples/
├── harnesses/
│   ├── codex/
│   │   ├── config.toml.example
│   │   └── README.md
│   ├── claude/
│   │   └── README.md
│   └── gemini/
│       ├── settings.json.example
│       └── README.md
├── src/
│   └── sqlctx/
│       ├── core/
│       ├── application/
│       ├── adapters/
│       │   ├── sqlserver/
│       │   ├── mysql/
│       │   ├── mariadb/
│       │   ├── oracle/
│       │   └── postgres/
│       ├── security/
│       ├── formatting/
│       ├── classification/
│       ├── indexing/
│       ├── exporting/
│       ├── server/
│       │   ├── http/
│       │   └── mcp/
│       └── cli/
├── docs/
│   ├── spec/
│   │   ├── design-spec-v1.5.md
│   │   └── design-spec-v1.5.sha256
│   ├── implementation-state.md
│   ├── getting-started.md
│   ├── server-operations.md
│   ├── command-reference.md
│   ├── use-cases.md
│   ├── api-and-mcp-examples.md
│   ├── security.md
│   ├── troubleshooting.md
│   └── harnesses/
│       ├── codex.md
│       ├── claude-code.md
│       └── gemini-cli.md
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   └── harness/
├── fixtures/
├── examples/
├── scripts/
│   ├── python-preflight.ps1
│   └── python-preflight.sh
├── config/
│   └── examples/
│       └── profiles.yaml
├── .gitignore
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
└── README.md
```

The root-level `skills/sql-context-pack/SKILL.md` is canonical. Vendor directories may contain only manifests, connection templates, installation notes, and thin compatibility shims. They must not copy or fork the workflow instructions.

A separate repository may be considered only after v1 when a component has an independent release cadence, ownership boundary, or security boundary. Graph/ER rendering remains a later phase and is not a reason to split the v1 repository.

---

## 2. Product Boundary

### 2.1 Required scope

Version 1 must provide:

1. Database connection profiles configured and started by the owner.
2. Schema discovery for supported relational databases.
3. Preliminary name-based category discovery.
4. User-selectable materialization mode: ask, all categories, or selected categories.
5. Full extraction and analysis of every permitted object regardless of the selected output categories.
6. Final relationship-aware category classification after full extraction.
7. Table DDL extraction.
8. Stored procedure extraction.
9. Masked representative sample rows for tables.
10. SQLFluff formatting.
11. Unknown-category escalation to the owner.
12. Object, dependency, relationship, tag, and graph-ready indexes.
13. Boundary metadata for analyzed objects intentionally excluded from selective output.
14. HTTP API.
15. MCP tools and resources over the same application core.
16. Paginated sitemap and resumable batch extraction/export.
17. Configurable client-side output directory.
18. Separate analysis-completeness and materialization-completeness validation.
19. No database username or password exposed to the agent/model.
20. No persistent temporary directories created inside the target project.
21. SQLFluff install, status, and explicit update lifecycle.
22. Detailed reports for partial failures without stopping the whole export.
23. One canonical open-format Agent Skill with tested packaging for Codex, Claude Code, and Gemini CLI.
24. A complete operator and user documentation set with case-by-case command examples.
25. SemVer version bumping, `CHANGELOG.md` maintenance, and cross-file release-version validation for every change.
26. Complete HTTP/MCP contract schemas, behavior mapping, and representative request/response examples.
27. A provider-neutral classification boundary in which the server supplies sanitized evidence and the active harness/model may submit non-authoritative proposals.

### 2.2 Explicitly out of scope for v1

Do not add these features unless separately requested:

- Natural-language querying of production data.
- Arbitrary SQL execution.
- INSERT, UPDATE, DELETE, DDL mutation, or migration execution.
- Automatic database schema changes.
- Vector database or embedding generation.
- Full text-to-SQL.
- Web administration UI.
- Cloud-hosted credential vault.
- Automatic business-category invention without owner confirmation.
- ER diagram rendering itself; v1 only produces graph-ready metadata.
- Continuous database change-data capture.
- Background scheduling.
- Database backup/restore.
- Direct calls from `sqlctx-server` to Codex, Claude, Gemini, or other model-provider APIs.
- Installation or management of the Codex, Claude Code, or Gemini CLI executables themselves.
- Separate provider-specific copies of the core classification/export workflow.

---

## 3. Core Architectural Decision

### 3.1 Choose Python, not Node.js

Use Python as the primary implementation language.

#### Reasons

1. SQLFluff is a Python package and can be called through its Python module or CLI without introducing a second runtime.
2. The system requires database adapters, masking, structured export, filesystem packaging, FastAPI, and MCP integration; all are available in one Python runtime.
3. A Node.js implementation would still require Python or an external SQLFluff executable, creating:
   - runtime coordination,
   - subprocess error handling,
   - duplicated dependency management,
   - cross-platform PATH issues,
   - more complex installation and update behavior.
4. Python provides mature database drivers for SQL Server, MySQL, MariaDB, Oracle, and PostgreSQL.
5. The official MCP Python SDK supports tools, resources, and common transports.

### 3.2 High-level component diagram

```text
Owner
  │
  ├─ creates connection profile
  ├─ provides credentials through environment/secret mechanism
  └─ starts sqlctx-server
          │
          ▼
Agent Skill / CLI / MCP Client
          │
          ├─ HTTP API
          └─ MCP tools/resources
                  │
                  ▼
          SQL Context Application Core
          ├─ Connection Profile Resolver
          ├─ Database Adapter Registry
          ├─ Name Inventory and Category Preview
          ├─ Materialization Selection Manager
          ├─ Full DDL/Routine Extractor
          ├─ Sample Extractor
          ├─ Sensitive Data Classifier
          ├─ Masking/Pseudonymization Engine
          ├─ SQLFluff Manager
          ├─ Two-pass Category Classifier
          ├─ Dependency and Relationship Analyzer
          ├─ Materialization Planner
          ├─ Graph Metadata Builder
          ├─ Export Job Manager
          └─ Bundle/Manifest Writer
                  │
                  ▼
             Read-only Database
```

### 3.3 One core, two interfaces

HTTP and MCP must call the same application services. Business rules must not be duplicated in route handlers or MCP tool functions.

```text
HTTP Router ─┐
             ├─> Application Services ─> Domain/Core ─> Adapters
MCP Server ──┘
```

### 3.4 One canonical Skill, three harness packages

The harness architecture is:

```text
                         skills/sql-context-pack/SKILL.md
                                      │
                  canonical workflow, inputs, outputs, safety rules
                                      │
             ┌────────────────────────┼────────────────────────┐
             ▼                        ▼                        ▼
   Codex plugin/config       Claude plugin/config     Gemini extension/config
             │                        │                        │
             └────────────────────────┼────────────────────────┘
                                      ▼
                     owner-started SQL Context Pack MCP
                                      ▼
                         shared Python application core
```

Harness packages must differ only where the host requires a different manifest, discovery path, MCP configuration shape, or invocation syntax. The following behavior must remain byte-for-byte equivalent where practical and semantically equivalent otherwise:

- selection intent parsing,
- pagination loops,
- model-proposal handling,
- owner clarification,
- batching and resumability,
- bundle validation and assembly,
- final completeness checks,
- prohibited behaviors.

### 3.5 Deterministic server and model-harness responsibility boundary

The Python server must not embed a model-provider SDK or model API credential.

Responsibilities:

```text
Server/core:
  - catalog discovery and extraction
  - masking before serialization
  - deterministic Pass 1 rules
  - relationship/dependency evidence construction
  - deterministic Pass 2 rules
  - proposal validation and provenance recording
  - owner override persistence
  - materialization, export, and validation

Active Agent Skill/model:
  - interpret the user's output intent
  - review only sanitized classification evidence
  - submit optional semantic classification proposals
  - present unresolved trade-offs to the owner
  - never mark its own proposal as an owner decision

Owner:
  - configure credentials and start the service
  - approve sensitive lifecycle operations
  - decide genuinely ambiguous business categories
```

This separation makes the same server work with Codex, Claude, Gemini, or no model at all. Model proposals may improve classification recall, but their absence must not prevent deterministic extraction, indexing, or export.

---

## 4. Security Model

### 4.1 Credential ownership

The owner must configure credentials and start the server before an agent uses it.

The model/agent may know only:

- connection profile name,
- database engine,
- permitted schema names,
- server capability metadata,
- export job identifiers.

The model/agent must never receive:

- database hostname when hidden by policy,
- database username,
- database password,
- connection string,
- client secret,
- private key,
- credential-bearing environment variables.

### 4.2 Connection profiles

Default profile location:

```text
Linux/macOS: ~/.config/sql-context-pack/profiles.yaml
Windows:     %APPDATA%\sql-context-pack\profiles.yaml
```

A profile must reference environment-variable names rather than storing a password directly.

```yaml
profiles:
  agrimap-readonly:
    engine: sqlserver
    host_env: SQLCTX_AGRIMAP_DB_HOST
    port: 1433
    database_env: SQLCTX_AGRIMAP_DB_NAME
    username_env: SQLCTX_AGRIMAP_DB_USER
    password_env: SQLCTX_AGRIMAP_DB_PASSWORD
    allowed_schemas:
      - agrimap_app
    allowed_object_types:
      - table
      - procedure
    sample_rows_per_table: 10
    max_sample_rows_per_table: 20
    masking_policy: strict
```

Rules:

- Never return resolved environment values through an API.
- Never log resolved environment values.
- Never serialize a complete connection string.
- Error responses must reference the profile name, not the credentials.
- A profile file containing a raw password must be rejected by default.
- Optional OS keychain integration may be added later but is not required in v1.

### 4.3 Database account

Require a dedicated read-only database account.

The account should have only:

- metadata/catalog read permission,
- object-definition read permission,
- SELECT permission on explicitly allowed schemas/tables.

It must not have:

- INSERT,
- UPDATE,
- DELETE,
- ALTER,
- CREATE,
- DROP,
- EXECUTE unless metadata extraction genuinely requires it,
- server administration permission.

### 4.4 No arbitrary SQL

Do not expose a generic endpoint such as:

```text
POST /query
POST /execute
tool: run_sql
```

All SQL must be generated from reviewed adapter templates.

Identifiers must be:

- discovered from the database catalog, or
- validated against the discovered catalog,
- quoted by the database adapter,
- never concatenated directly from untrusted input.

### 4.5 Server network boundary

Default behavior:

```text
Host: 127.0.0.1
Remote access: disabled
Authentication: separate random agent bearer token and owner control credential
HTTP API: owner-started
MCP transport: owner-started Streamable HTTP on the same loopback service
```

The owner must start `sqlctx-server` before an agent uses it. Harness configuration may connect to the already-running loopback MCP endpoint, but the model must not construct a database credential-bearing launch command.

Local credential handoff is normative:

1. On first start, generate two independent credentials: an `agent`-scoped bearer token for the harness and an `owner` control credential for privileged local operations.
2. Write the agent token with MCP URL and non-secret connection metadata into the owner-approved harness metadata file. Store the owner credential in a separate control file that harness configuration never references.
3. Protect both files as owner-only: mode `0600` on POSIX; an ACL limited to the current user and `SYSTEM` on Windows.
4. Server stdout may show only the MCP URL and agent-metadata path. It must never print either credential or the owner-control path.
5. Harness configuration consumes only the agent token through the approved bootstrap/configuration command. The local `sqlctx` control CLI unlocks the owner credential only after interactive owner presence (OS keychain/user-presence prompt or owner passphrase) for privileged approval/execution; grant must refuse piped/non-interactive input and offers no `--yes`, environment-variable, or agent-readable auto-approval path.
6. The Skill/model must never read, display, request, log, or pass either credential as a tool argument or command-line argument.
7. Connection examples may contain environment-variable or metadata-path references, never a literal credential.

For remote HTTP mode:

- TLS is mandatory.
- Authentication/authorization is mandatory.
- Apply least-privilege scopes.
- Add per-client rate limiting.
- Record audit metadata without recording sample values.
- Do not enable remote mode by default.

For optional local MCP STDIO mode:

- obtain database credentials from the environment of the server process,
- never pass credentials as MCP tool parameters,
- keep STDIO disabled in the default package,
- require the owner to opt in to client-managed process lifecycle,
- document that the harness may automatically start the configured process at session startup,
- treat a harness-spawned process as owner-authorized only when the owner created or approved its configuration,
- never let the model print, inspect, or rewrite credential-bearing environment entries.

Trade-off decision: owner-started Streamable HTTP is the v1 default because it satisfies the requirement that the owner configure credentials and run the service in advance. STDIO is retained only as an explicit convenience mode because Codex, Claude Code, and Gemini CLI can manage STDIO child processes differently.

### 4.6 Audit log

The audit log may include:

- timestamp,
- profile name,
- caller identity or local session identifier,
- requested schemas,
- object count,
- exported object IDs,
- row counts,
- masking rule IDs,
- result status,
- durations,
- content hashes.

The audit log must not include:

- raw DDL literals identified as secrets,
- raw sample values,
- passwords,
- tokens,
- connection strings.

---

## 5. Sensitive Data Cleansing

### 5.1 Cleansing must happen before serialization

Raw values must be classified and transformed inside the server process before they are:

- returned by HTTP,
- returned by MCP,
- written to an export file,
- placed in a bundle,
- written to a log,
- included in an exception message.

### 5.2 Detection layers

Apply detection in this order:

1. **Owner policy override**
2. **Database-native classification metadata**, where available
3. **Exact column-name rule**
4. **Column-name token rule**
5. **Data type and length heuristic**
6. **Value-pattern detector**
7. **Routine/DDL secret scanner**
8. **Conservative fallback**

Do not let an LLM inspect raw values to decide whether they are sensitive.

### 5.3 Required sensitive classes

At minimum:

```text
national_id
passport
username
password
password_hash
secret
secret_key
private_key
api_key
client_secret
access_token
refresh_token
jwt
session_token
cookie
email
phone
address
personal_name
financial_account
credit_card
date_of_birth
precise_location
biometric
```

### 5.4 Default transformations

| Class | Default transformation | Example output |
|---|---|---|
| national_id | format-preserving partial mask or synthetic alias | `1102001xxxxxx` |
| username | deterministic synthetic alias | `user_k7m2q9x4p1` |
| password | full replacement; never partial | `[REDACTED:PASSWORD]` |
| password_hash | full replacement with algorithm hint only | `[REDACTED:HASH:bcrypt]` |
| secret_key | full replacement | `[REDACTED:SECRET_KEY]` |
| api_key | full replacement | `[REDACTED:API_KEY]` |
| access_token | full replacement | `[REDACTED:ACCESS_TOKEN]` |
| refresh_token | full replacement | `[REDACTED:REFRESH_TOKEN]` |
| jwt | full replacement | `[REDACTED:JWT]` |
| email | deterministic synthetic alias | `user_k7m2q9x4p1@example.invalid` |
| phone | format-preserving mask | `08xxxx1234` |
| personal_name | deterministic synthetic alias | `PERSON_0007` |
| address | generalized replacement | `[REDACTED:ADDRESS]` |
| credit_card | keep last four only | `xxxxxxxxxxxx4242` |
| binary/blob | omit content and report length | `<BINARY length=2048>` |

A fake password-like value such as `dPtv2oGEZLFvC3G1yiftC...` may be generated only when the policy explicitly requests **synthetic format preservation**. The default is a clear redaction marker, because fake secrets that look valid can be mistaken for real credentials.

### 5.5 Referential consistency

When a value is used as a business key or relation key, masking must remain stable within the snapshot.

Use deterministic pseudonymization and explicit alias encoding:

```text
digest = HMAC-SHA256(snapshot_masking_key, normalized_value)
token  = lowercase(CrockfordBase32(digest))[0:N]
```

Map the result to the target format, for example:

```text
username -> user_k7m2q9x4p1
email    -> user_k7m2q9x4p1@example.invalid
```

Requirements:

- The masking key must never be included in the export.
- The same raw value within one snapshot must map to the same alias.
- Different snapshots should use different keys by default.
- Owner-configured stable keys may be supported, but must remain outside the model context.
- Foreign-key values must retain join consistency after transformation.
- Sequence numbers based on query order are forbidden because they drift across pagination, retry, and process restart.
- Use the Crockford Base32 alphabet so examples containing decimal digits such as `user_k7m2q9x4p1` are valid and unambiguous; do not silently switch alphabets between processes.
- Maintain a protected per-snapshot alias registry in the runtime store so resume produces the same alias and collisions can be detected.
- Registry entries contain the full keyed digest, alias namespace, and chosen alias—not the normalized raw value.
- A resumable snapshot must reuse the exact same `snapshot_masking_key` after a process restart. Generating a new key while retaining the registry is forbidden because the stored digests would no longer match.
- Store the snapshot key envelope-encrypted in the protected runtime store under an owner-held runtime master key, or derive it deterministically from an owner-held secret plus the immutable snapshot ID. The owner-held secret never enters model context.
- If secure key persistence/derivation is unavailable, mark the snapshot `cross_process_resumable=false` and reject cross-process resume explicitly; never claim resumability and silently rotate the key.
- Retain the protected snapshot key and alias registry while the catalog or any dependent export is active or unexpired, then destroy them during eligible catalog cleanup.
- On collision, extend `N` deterministically for all colliding entries until the aliases are unique; do not assign a sequence suffix.
- Never export raw values, the masking key, or the secret alias registry. Only the resulting aliases may enter output artifacts.

### 5.6 Fail-closed behavior

If a value is probably sensitive but cannot be classified confidently:

- do not emit the raw value,
- replace it with `[REDACTED:UNCLASSIFIED]`,
- add a warning to the masking report,
- allow the owner to add a policy override and rerun.

### 5.7 Stored procedure and DDL source scanning

Sensitive values can be hardcoded inside routines, comments, default constraints, or dynamic SQL.

Before writing routine/DDL text:

- scan quoted string literals,
- scan comments,
- detect JWTs, API keys, connection strings, passwords, and private keys,
- replace only the literal content while preserving valid SQL quoting,
- record the transformation location and detector rule in metadata,
- never write the original literal to the report.

Example:

```sql
SET @API_KEY = '[REDACTED:API_KEY]';
```

---

## 6. Database and SQLFluff Dialect Mapping

| Database engine key | SQLFluff dialect | Initial Python driver | Notes |
|---|---|---|---|
| `sqlserver` | `tsql` | `pyodbc` | SQL Server / T-SQL |
| `mysql` | `mysql` | `pymysql` or `mysql-connector-python` | Keep separate from MariaDB adapter |
| `mariadb` | `mariadb` | MariaDB Connector/Python or compatible reviewed driver | SQLFluff dialect inherits from MySQL but must remain a distinct adapter |
| `oracle` | `oracle` | `oracledb` | SQLFluff Oracle dialect includes PL/SQL |
| `postgres` | `postgres` | `psycopg` | `psql` is a client command, not the SQLFluff dialect name |

At runtime, expose the installed canonical dialect list from:

```bash
sqlfluff dialects
```

Do not hardcode a claim that a dialect is supported without verifying it against the installed SQLFluff version.

---

## 7. Database Adapter Contract

Each adapter must implement this interface conceptually:

```python
class DatabaseAdapter(Protocol):
    engine: str
    sqlfluff_dialect: str

    async def test_connection(self, profile: ResolvedProfile) -> ConnectionTestResult: ...
    async def get_server_info(self, profile: ResolvedProfile) -> ServerInfo: ...
    async def list_schemas(self, profile: ResolvedProfile) -> list[SchemaInfo]: ...
    async def discover_objects(
        self,
        profile: ResolvedProfile,
        request: DiscoveryRequest,
    ) -> AsyncIterator[DatabaseObject]: ...
    async def get_table_definition(
        self,
        profile: ResolvedProfile,
        object_ref: ObjectRef,
    ) -> SqlDefinition: ...
    async def get_procedure_definition(
        self,
        profile: ResolvedProfile,
        object_ref: ObjectRef,
    ) -> SqlDefinition: ...
    async def get_table_columns(
        self,
        profile: ResolvedProfile,
        object_ref: ObjectRef,
    ) -> list[ColumnMetadata]: ...
    async def get_constraints(
        self,
        profile: ResolvedProfile,
        object_ref: ObjectRef,
    ) -> list[ConstraintMetadata]: ...
    async def get_foreign_keys(
        self,
        profile: ResolvedProfile,
        object_ref: ObjectRef,
    ) -> list[ForeignKeyMetadata]: ...
    async def get_sample_rows(
        self,
        profile: ResolvedProfile,
        request: SampleRequest,
    ) -> SamplePage: ...
    async def get_routine_dependencies(
        self,
        profile: ResolvedProfile,
        object_ref: ObjectRef,
    ) -> list[DependencyEdge]: ...
```

### 7.1 SQL Server metadata sources

Use reviewed queries against:

```text
sys.schemas
sys.tables
sys.columns
sys.types
sys.indexes
sys.index_columns
sys.key_constraints
sys.foreign_keys
sys.foreign_key_columns
sys.procedures
sys.sql_modules
sys.parameters
sys.extended_properties
sys.sql_expression_dependencies
```

Use `OBJECT_DEFINITION()` or `sys.sql_modules.definition` for routines where permitted.

### 7.2 MySQL metadata sources

Use reviewed queries against:

```text
information_schema.SCHEMATA
information_schema.TABLES
information_schema.COLUMNS
information_schema.TABLE_CONSTRAINTS
information_schema.KEY_COLUMN_USAGE
information_schema.ROUTINES
information_schema.PARAMETERS
```

Use engine-supported `SHOW CREATE TABLE` and reviewed routine-definition retrieval where privileges permit.

### 7.3 MariaDB metadata sources

Use a distinct MariaDB adapter even where catalog structures resemble MySQL.

Reasons:

- syntax and metadata behavior can diverge,
- SQLFluff exposes a distinct `mariadb` dialect,
- future MariaDB-specific behavior must not be hidden in a MySQL conditional.

### 7.4 Oracle metadata sources

Use owner-permitted views and APIs such as:

```text
ALL_USERS or USER_USERS
ALL_TABLES
ALL_TAB_COLUMNS
ALL_CONSTRAINTS
ALL_CONS_COLUMNS
ALL_PROCEDURES
ALL_ARGUMENTS
ALL_SOURCE
ALL_DEPENDENCIES
DBMS_METADATA.GET_DDL
```

Capabilities must be discovered at runtime because access to `DBMS_METADATA` and `ALL_*` views varies by account privileges.

### 7.5 PostgreSQL metadata sources

Use reviewed queries against:

```text
pg_catalog.pg_namespace
pg_catalog.pg_class
pg_catalog.pg_attribute
pg_catalog.pg_constraint
pg_catalog.pg_index
pg_catalog.pg_proc
pg_catalog.pg_depend
information_schema.columns
information_schema.routines
```

Use functions such as:

```text
pg_get_functiondef
pg_get_viewdef
pg_get_constraintdef
pg_get_indexdef
```

### 7.6 Capability negotiation

Each adapter must return:

```json
{
  "engine": "sqlserver",
  "server_version": "masked-or-policy-allowed",
  "sqlfluff_dialect": "tsql",
  "supports": {
    "tables": true,
    "procedures": true,
    "functions": false,
    "packages": false,
    "routine_dependencies": true,
    "native_comments": true,
    "native_classification": false,
    "deterministic_sampling": true
  },
  "limits": {
    "max_batch_objects": 25,
    "recommended_batch_objects": 10,
    "max_sample_rows_per_table": 20
  }
}
```

The skill must use capabilities instead of assuming every engine supports the same object types.

---

## 8. Sample Data Strategy

### 8.1 Required row count

Default:

```text
minimum requested sample target per eligible table = 10
default requested sample target per eligible table = 10
```

Normative interpretation of the raw phrase “at least 10 rows per table”:

- A normal export request must not set `rows_per_table` below 10.
- If at least 10 eligible source rows exist, the export must contain at least 10; the v1 default contains exactly 10.
- The owner may request more than 10 up to `max_sample_rows_per_table`.
- Fewer than 10 is valid only when the table contains fewer eligible rows, access/policy removes rows, or extraction fails; the shortage must be explicit.
- The system must never duplicate or fabricate rows to satisfy the target.

Behavior:

- If the table contains at least the requested target, export exactly the requested target.
- If the table contains fewer rows than the requested target, export the actual available count.
- Never duplicate or fabricate production rows merely to reach 10.
- Record `requested_count`, `actual_count`, and `shortage_reason`.
- If policy removes every eligible row, emit zero rows and a masking/policy warning.
- Reject a user or profile request below 10 with `400 INVALID_SAMPLE_TARGET`; policy-driven shortages are reported results, not invalid requests.

### 8.2 Deterministic selection

Default strategy:

1. Use the primary key ordered ascending.
2. Otherwise use the first unique non-null index.
3. Otherwise use a deterministic engine-supported physical or hash strategy where safe.
4. Otherwise return a non-deterministic sample and mark it explicitly.

Do not use expensive full-table random sorting by default.

### 8.3 Query limits

- Select only required columns.
- Limit text values by configured maximum length.
- Do not read large binary/LOB values by default.
- Apply database statement timeout.
- Cancel the query if the client cancels the job.
- Use a small connection pool.
- Use a configurable concurrency limit.
- Do not issue `COUNT(*)` on every large table merely to know whether ten rows exist.
- Fetch `target + 1` only when needed to detect continuation.

### 8.4 Sample representation inside table SQL

The table `.sql` file must remain valid SQL and self-contained for an AI model.

Processing order:

1. Extract DDL.
2. Clean sensitive literals in DDL.
3. During final materialization only, format the SQL definition with SQLFluff.
4. Append masked sample rows as SQL line comments.
5. Do not run SQLFluff over the appended sample block.

Example:

```sql
CREATE TABLE [agrimap_app].[UM_USER] (
    [ID] NUMERIC(38, 0) NOT NULL,
    [USERNAME] NVARCHAR(100) NOT NULL,
    [PASSWORD] NVARCHAR(255) NOT NULL
);

-- @sqlctx-samples-begin requested=10 actual=10 masked=true
-- {"ID":1,"USERNAME":"user_k7m2q9x4p1","PASSWORD":"[REDACTED:PASSWORD]"}
-- {"ID":2,"USERNAME":"user_r4v8c2n6d0","PASSWORD":"[REDACTED:PASSWORD]"}
-- @sqlctx-samples-end
```

Additional metadata belongs in the object index, not in arbitrary prose inside the SQL body.

---

## 9. SQLFluff Lifecycle

### 9.1 Installation guarantee

A Skill Markdown file alone cannot guarantee installation. The repository must include executable bootstrap logic.

Cross-harness guarantee boundary:

- Installing a plugin/extension manifest alone must not be claimed to execute Python package installation; Codex, Claude Code, and Gemini CLI have different lifecycle and consent behavior.
- Installing the Python package with the owner's selected host Python installs the pinned SQLFluff dependency into that interpreter's normal/user package location; sqlctx does not create another Python environment.
- On the first Skill invocation, every harness must call `sqlctx_sqlfluff_status` and then `sqlctx_sqlfluff_ensure` before formatting.
- If installation needs network/process consent, the Skill asks once and resumes after approval.
- A successful `ensure` is cached and later runs must not reinstall the same pinned version.
- Therefore the product guarantees “available before first formatting,” not “silently installed merely by copying a plugin.”

Use both layers:

1. Declare SQLFluff as a pinned Python dependency in `pyproject.toml`.
2. Add an `ensure` command that verifies or, after owner approval, installs the package through the same selected host interpreter before formatting.
3. Never create, copy, activate, or manage a `venv`, virtualenv, conda environment, pipx environment, or bundled Python inside the Skill, target project, or sqlctx runtime directory.

Required commands:

```bash
sqlctx doctor
sqlctx sqlfluff status
sqlctx sqlfluff ensure
sqlctx sqlfluff update
sqlctx sqlfluff update --version X.Y.Z
```

### 9.2 Host Python selection and state

Production uses Python already installed on the user's machine. Resolve it in this order:

1. owner-configured absolute `python_executable` in protected sqlctx configuration;
2. otherwise `sys.executable` of the process running `sqlctx`/`sqlctx-server`.

The interpreter must report Python `>=3.11`. Resolve and compare the canonical absolute executable path; never fall back to a different `python`/`python3` found later on `PATH` during formatting.

Store only tooling state—not an interpreter or environment—outside the project:

```json
{
  "python_executable": "<protected-absolute-host-python>",
  "python_executable_fingerprint": "sha256:host-python-...",
  "python_version": "3.11+",
  "environment_owner": "host_or_owner",
  "sqlfluff_version": "X.Y.Z",
  "tooling_fingerprint": "sha256:python-version-plus-sqlfluff-plus-config-...",
  "installed_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "install_source": "locked-dependency"
}
```

The absolute path is protected operational state; public HTTP/MCP status returns only its fingerprint, Python version, SQLFluff version, and tooling fingerprint.

Ownership rules:

- sqlctx must not create or own a Python environment.
- The default supported auto-install path is the selected base interpreter's user site using `<host-python> -m pip install --user ...`.
- If the owner explicitly selects an already-existing virtual/conda environment, sqlctx may verify and execute its preinstalled SQLFluff but must not create, update, repair, activate, or delete that environment; missing/mismatched SQLFluff returns `OWNER_MANAGED_PYTHON_ENVIRONMENT` with manual guidance.
- Never use `sudo pip`, `--break-system-packages`, pipx, or a project-local install to bypass host packaging policy.

### 9.3 Ensure algorithm

```text
1. Resolve and validate the configured host interpreter; require Python >=3.11.
2. Check SQLFluff metadata and run `<host-python> -m sqlfluff version` through that exact executable.
3. Compare with the centrally pinned version and formatter configuration.
4. If valid, reuse it and do not install.
5. If missing or mismatched on a supported base interpreter:
   a. acquire a cross-process installation lock;
   b. recheck after lock acquisition;
   c. request owner approval when package/network access is needed;
   d. run `<host-python> -m pip install --user "sqlfluff==<PINNED_VERSION>"`;
   e. verify import and CLI through the same interpreter;
   f. write tooling state atomically.
6. If the interpreter is owner-managed, user-site is disabled, pip is unavailable, or host policy rejects the install, do not create an environment or bypass policy; return `TOOLING_UNAVAILABLE` with manual remediation.
7. If installation fails, preserve the prior installation/state and return a sanitized error.
8. Never silently install `latest` during a normal export.
```

### 9.4 Update algorithm

An update must occur only after the request-bound owner approval defined in Section 12.4 (or direct owner-control CLI execution).

```text
1. Resolve requested version or latest stable version.
2. Acquire the cross-process tooling lock and reject with `409 TOOLING_BUSY` if any export or formatting job is active.
3. Resolve the same host interpreter, validate Python >=3.11, and record the currently working SQLFluff version.
4. Reject automated update for an owner-managed existing environment or a host policy that forbids user-site installation.
5. Run `<host-python> -m pip install --user --upgrade "sqlfluff==<REQUESTED_VERSION>"`.
6. Verify import, CLI version, dialects, parse/format self-tests, and tooling fingerprint through the same interpreter.
7. Atomically update tooling state only after verification.
8. On failure, reinstall the recorded previous exact version through the same host interpreter and verify rollback.
9. If rollback also fails, return `TOOLING_BROKEN` with manual repair guidance; never create a fallback environment.
10. Do not modify the Skill directory, target project, or sqlctx runtime directory with Python packages.
```

### 9.5 Required formatting command semantics

Equivalent configuration:

```bash
<host-python> -m sqlfluff format \
  --exclude-rules "CP02,LT01,RF06" \
  --dialect tsql \
  --templater raw \
  XXXXX.sql
```

For other engines, replace only the dialect:

```text
sqlserver -> tsql
mysql     -> mysql
mariadb   -> mariadb
oracle    -> oracle
postgres  -> postgres
```

### 9.6 Do not format a directory in production orchestration

Although SQLFluff accepts a directory, the exporter must invoke SQLFluff **one file at a time**.

Reason:

- one unparsable file must not prevent other files from being formatted,
- each file needs its own result, timing, error, and fallback,
- the original file must remain available when formatting fails.

### 9.7 Per-file fail-isolated algorithm

```text
SQLFluff runs only after Pass 2 and owner resolution, and only for SQL files included in final materialization. Full extraction and relationship analysis use sanitized, unformatted definitions. Analysis-failed objects never enter the formatting scope.

For every materialized SQL object:

1. Keep the cleaned, unformatted SQL in memory.
2. Create a temporary file in the operating-system temp directory.
3. Run:
   <host-python> -m sqlfluff parse --dialect <dialect> --templater raw <temp-file>
4. If parse fails:
   a. do not format;
   b. preserve the cleaned original SQL;
   c. mark format_status=parse_failed;
   d. capture sanitized diagnostics;
   e. continue to the next file.
5. If parse succeeds:
   a. run `<host-python> -m sqlfluff format` on the temporary file;
   b. run parse again through the same host interpreter;
   c. if post-format parse succeeds, use formatted content;
   d. otherwise use the cleaned original content and mark rollback.
6. Append the masked sample comment block after formatting.
7. Write the final target file atomically.
8. Delete the OS temporary file in a finally block.
9. Continue regardless of a single-object failure.
```

At export creation, record the immutable `tooling_fingerprint`. Because update is blocked while an export/format job is active, every file in that job sees the same host Python, SQLFluff version, and formatter configuration. If external mutation changes the fingerprint mid-job, stop remaining formatting with `TOOLING_CHANGED`; do not continue under mixed versions.

Never enable:

```text
fix_even_unparsable
--FIX-EVEN-UNPARSABLE
```

### 9.8 Exit-code handling

Interpret SQLFluff subprocess results explicitly:

```text
0 = success
1 = operation completed with violations or parse issue
2 = operational/configuration/internal error
```

Do not treat every non-zero result as the same failure type.

### 9.9 No project temporary directories

The system must not create these inside the output project:

```text
.tmp-sql-finalizer/
.tmp-sqlfluff-format/
.tmp-*/
```

Temporary work must use:

```python
tempfile.TemporaryDirectory()
```

or the platform-specific user cache/runtime directory.

Cleanup requirements:

- use `try/finally`,
- close file handles before cleanup on Windows,
- retry deletion for transient file locks,
- run stale OS-temporary-file cleanup on startup,
- never leave a partially extracted target directory.

Optional defensive `.gitignore` entry:

```gitignore
# Defensive only; sqlctx must not create these in the project.
.tmp-*/
.sqlctx-staging/
```

The `.gitignore` entry is a final defense, not a substitute for correct cleanup.

### 9.10 Python-unavailable preflight and installation guidance

The repository must provide non-Python detection helpers:

```text
scripts/python-preflight.ps1
scripts/python-preflight.sh
```

They only detect and explain; they must not install Python, request elevation, create an environment, or modify PATH. If no acceptable interpreter is found, return `PYTHON_UNAVAILABLE` with `required_version: ">=3.11"` and the relevant guide.

| Platform | Owner installation guidance | Verify and resolve the interpreter |
|---|---|---|
| Windows | Install a supported CPython from `https://www.python.org/downloads/windows/` or the official Python Install Manager. The owner decides installation scope and PATH integration. | `py -3 --version`; then `py -3 -c "import sys; print(sys.executable)"` |
| macOS | Use the signed installer from `https://www.python.org/downloads/macos/` or an owner-approved package manager that provides Python >=3.11. | `python3 --version`; then `python3 -c 'import sys; print(sys.executable)'` |
| Linux | Use the distribution's supported package manager to install Python >=3.11 and pip, or follow the official Python source/install guidance when the distribution is older. Do not replace or mutate an OS-owned Python installation. | `python3 --version`; then `python3 -c 'import sys; print(sys.executable)'` |

After Python installation, the owner must:

1. verify the reported version is at least 3.11;
2. record the resolved absolute executable in protected sqlctx configuration when it differs from the server's `sys.executable`;
3. run `<host-python> -m pip --version`;
4. inspect `<host-python> -m site --user-site` and ensure the user site is enabled;
5. follow `https://packaging.python.org/en/latest/tutorials/installing-packages/` if pip/user-site setup needs manual repair;
6. rerun the non-Python preflight, install `sql-context-pack` through that interpreter, then run `sqlctx doctor` and `sqlctx sqlfluff ensure`.

Do not recommend pipx for this product because pipx creates its own virtual environment. Do not use `curl ... | python`, `sudo pip`, `--break-system-packages`, or an automatic Python installer from the Skill.

---

## 10. Catalog, Category Selection, Full Analysis, Pagination, and Batch Export

### 10.1 Analysis scope and materialization scope are different

The system must maintain two independent scopes:

```text
analysis_scope:
  Every permitted table and stored procedure in the selected schemas.

materialization_scope:
  The objects whose SQL files are written into the user-selected project output.
```

Mandatory invariant:

```text
A category selection may reduce materialization_scope.
It must never reduce analysis_scope.
```

This prevents false exclusions caused by ambiguous names such as:

- an object whose prefix appears unrelated but is connected to the selected domain,
- a generic object such as `CONTENT`, `CONFIG`, `MASTER`, or `TRANSACTION`,
- a procedure whose name does not identify every table it reads or writes,
- a shared table used by more than one business category.

The full sanitized snapshot used for analysis stays in the server runtime store. It is not automatically written into the target project.

### 10.2 Two-pass classification lifecycle

The catalog lifecycle must run in this order.

#### Pass 1 — Preliminary classification

Use only inexpensive catalog information:

- schema,
- object type,
- object name,
- configured exact-name rules,
- configured prefix/token rules,
- database-native comments that are available without full object extraction.

Do not use:

- table samples,
- complete DDL,
- procedure bodies,
- foreign-key graph,
- procedure read/write dependencies.

Pass 1 exists only to create a category preview for the user. It must be labeled `preliminary`, not final.

#### User materialization selection

After Pass 1, the user may choose:

```text
all:
  Materialize every final category.

selected:
  Materialize only the categories selected by the user after Pass 2.

ask:
  Pause after category preview and ask whether to materialize all
  categories or selected categories.
```

`ask` is the default when the user has not explicitly requested all categories or named selected categories.

The selection records intent only. The server must still continue with full extraction of every permitted object.

#### Full extraction

After selection is recorded, extract and sanitize all permitted objects:

- table DDL,
- columns and data types,
- keys and constraints,
- foreign keys,
- stored procedure definitions,
- routine parameters,
- routine dependencies,
- up to the permitted sample-row count,
- database comments and extended metadata.

The extraction must be paginated, batched, resumable, and fail-isolated.

#### Pass 2 — Final contextual classification

Reclassify every object using the complete sanitized context:

- Pass 1 evidence,
- column names and data types,
- primary and foreign keys,
- incoming and outgoing relationships,
- stored procedure read/write/call edges,
- table and routine descriptions,
- sanitized sample shape and representative values,
- owner overrides.

An object may move to a different category between Pass 1 and Pass 2.

Examples:

```text
Pass 1:
  APP_OBJECT -> unresolved

Pass 2:
  APP_OBJECT has FK to CONTENT and is written by CONTENT_I
  -> final category: content
```

```text
Pass 1:
  CONTENT_CONFIG -> content

Pass 2:
  used only by global platform configuration procedures
  -> final category: platform_config
```

#### Materialization planning

Apply the user's selected category names to the **final Pass 2 categories**, not to the preliminary object list.

This guarantees:

- an initially ambiguous object that becomes `content` is included when `content` was selected,
- an initially `content`-looking object that becomes another category is excluded from strict `content` output,
- category selection remains semantically meaningful after relationship analysis.

### 10.3 Materialization modes

Request model:

```json
{
  "mode": "ask",
  "selected_categories": []
}
```

Allowed `mode` values:

| Mode | Behavior |
|---|---|
| `ask` | Pause after Pass 1 and ask the owner to choose all or selected categories |
| `all` | Analyze all objects and materialize all final categories |
| `selected` | Analyze all objects and materialize only objects assigned to selected final categories |

Version 1 has one fixed dependency behavior: excluded related objects appear only as boundary nodes/edges in indexes; their SQL and samples are not written. This `index_only` behavior is an invariant, not a request field or user-selectable mode. Direct dependency or reachable-closure materialization is outside v1 scope.

### 10.4 Category preview

The category preview must contain only safe object descriptors and preliminary category evidence.

```json
{
  "catalog_id": "cat_01J...",
  "classification_pass": "preliminary",
  "analysis_scope": {
    "discovered_objects": 842
  },
  "items": [
    {
      "category": "um",
      "preliminary_count": 64,
      "examples": [
        "UM_USER",
        "UM_ROLE",
        "UM_USER_ROLE"
      ],
      "confidence_summary": {
        "confirmed_by_rule": 58,
        "suggested": 6
      }
    },
    {
      "category": "content",
      "preliminary_count": 103,
      "examples": [
        "CONTENT",
        "CONTENT_SHARE",
        "CONTENT_PERMISSION"
      ],
      "confidence_summary": {
        "confirmed_by_rule": 91,
        "suggested": 12
      }
    }
  ],
  "unresolved_count": 37,
  "page": {
    "limit": 100,
    "returned": 2,
    "next_cursor": null
  },
  "warning": "Preliminary categories may change after full relationship analysis."
}
```

The preview always uses the standard `items` plus `page: {limit, returned, next_cursor}` envelope, including a single-page result. HTTP and MCP tools must return the same normalized shape, and clients must continue until `next_cursor` is `null`.

### 10.5 Sitemap contents

A database may contain 100–1000 or more objects. The agent must not receive one huge response.

The sitemap must contain:

- object ID,
- engine,
- database/schema,
- object type,
- object name,
- preliminary category,
- final category when available,
- classification pass/status,
- materialization decision,
- inclusion/exclusion reason,
- dependency counts,
- extraction status,
- content fingerprint,
- estimated cost,
- recommended extraction/export batch.

Example final item:

```json
{
  "object_id": "table:agrimap_app.APP_OBJECT",
  "object_type": "table",
  "schema": "agrimap_app",
  "name": "APP_OBJECT",
  "preliminary_category": null,
  "final_category": "content",
  "classification_status": "suggested",
  "materialization": {
    "included": true,
    "reason": "final_category_selected"
  },
  "estimated_weight": 3
}
```

### 10.6 Cursor pagination

Use opaque cursors, not page numbers.

```json
{
  "items": [],
  "page": {
    "limit": 100,
    "returned": 100,
    "next_cursor": "opaque-cursor-or-null"
  },
  "batching": {
    "recommended_objects": 10,
    "maximum_objects": 25,
    "recommended_weight": 30
  }
}
```

Never stop merely because a page returned fewer items than requested. Stop only when `next_cursor` is null.

### 10.7 Skill pagination and completeness rule

The skill must maintain separate sets:

```text
all_discovered_object_ids
all_analyzed_object_ids
final_materialization_object_ids
intentionally_excluded_object_ids
failed_analysis_object_ids
```

Workflow:

```text
cursor = null
all_discovered_object_ids = []

repeat:
    call list_sitemap(cursor, limit, view="analysis")
    append returned object IDs
    cursor = next_cursor
until cursor is null

wait for full analysis to finish

cursor = null
final_materialization_object_ids = []

repeat:
    call list_sitemap(cursor, limit, view="materialization")
    append included object IDs
    record excluded object IDs and reasons
    cursor = next_cursor
until cursor is null

export only final_materialization_object_ids
verify:
    discovered = analyzed + failed_analysis
    analyzed = materialized + intentionally_excluded
```

In `all` mode, `intentionally_excluded` should normally be zero except for explicit security/policy exclusions.

In `selected` mode, discovered count and materialized count are expected to differ.

### 10.8 Weighted batching

Internal full extraction and final materialization exports must respect both:

- maximum object count,
- maximum estimated weight.

Suggested weights:

```text
simple table         = 1
wide table            = 2
table with samples    = 3
simple procedure      = 2
large procedure       = 5
Oracle package        = 8 (future)
```

A selected-category output must not cause the server to skip extraction of non-selected objects. It only reduces the final bundle content.

## 11. HTTP API Specification

Base path:

```text
/api/v1
```

### 11.1 Health

```http
GET /api/v1/health
```

Response `200`:

```json
{
  "status": "ok",
  "service": "sql-context-pack",
  "version": "1.1.0"
}
```

Must not test or expose every database profile.

### 11.2 Capabilities

```http
GET /api/v1/capabilities
```

Response `200`:

```json
{
  "engines": [
    {"engine":"sqlserver","sqlfluff_dialect":"tsql"},
    {"engine":"mysql","sqlfluff_dialect":"mysql"},
    {"engine":"mariadb","sqlfluff_dialect":"mariadb"},
    {"engine":"oracle","sqlfluff_dialect":"oracle"},
    {"engine":"postgres","sqlfluff_dialect":"postgres"}
  ],
  "object_types": ["table","procedure"],
  "interfaces": ["http","mcp"],
  "limits": {
    "sitemap_page_max": 250,
    "export_batch_max_objects": 25
  }
}
```

### 11.3 List safe profile descriptors

```http
GET /api/v1/profiles
```

Response:

```json
{
  "items": [
    {
      "profile": "agrimap-readonly",
      "engine": "sqlserver",
      "allowed_schemas": ["agrimap_app"],
      "ready": true
    }
  ]
}
```

Never return host, username, password, or connection string.

### 11.4 Test profile

```http
POST /api/v1/profiles/{profile}/test
```

Response `200`:

```json
{
  "profile": "agrimap-readonly",
  "reachable": true,
  "engine": "sqlserver",
  "capabilities": {
    "tables": true,
    "procedures": true
  }
}
```

### 11.5 Create catalog snapshot

```http
POST /api/v1/catalogs
Content-Type: application/json
Idempotency-Key: <required-client-generated-non-secret-key>
```

Input:

```json
{
  "profile": "agrimap-readonly",
  "schemas": ["agrimap_app"],
  "object_types": ["table", "procedure"],
  "include_patterns": [],
  "exclude_patterns": [],
  "category_policy": "two_pass",
  "selection": {
    "mode": "ask",
    "selected_categories": []
  },
  "sample": {
    "rows_per_table": 10,
    "strategy": "deterministic"
  },
  "masking_policy": "strict"
}
```

`Idempotency-Key` is required. It is scoped by authenticated caller and operation. Reusing a key with the same normalized request returns the original catalog job; reusing it with a different normalized request returns `409 IDEMPOTENCY_CONFLICT`. The application core stores the record for the catalog retention period, and HTTP and MCP use the same idempotency model.

Behavior:

- `ask`: perform name inventory and Pass 1, then pause at `awaiting_selection`.
- `all`: record all-category intent and continue to full extraction.
- `selected`: require at least one selected category and continue to full extraction.
- All modes use the same full `analysis_scope`.

Response `202`:

```json
{
  "catalog_id": "cat_01J...",
  "request_fingerprint": "sha256:catalog-request-...",
  "cross_process_resumable": true,
  "status": "queued",
  "status_url": "/api/v1/catalogs/cat_01J..."
}
```

### 11.5a Rediscover catalog jobs

```http
GET /api/v1/catalogs?status=ready&limit=100&cursor=...
```

Response:

```json
{
  "items": [
    {
      "catalog_id":"cat_01J...",
      "profile":"agrimap-readonly",
      "status":"ready",
      "request_fingerprint":"sha256:catalog-request-...",
      "selection_fingerprint":"sha256:selection-...",
      "request_summary":{"schemas":["agrimap_app"],"object_types":["table","procedure"],"include_patterns":[],"exclude_patterns":[],"rows_per_table":10,"sample_strategy":"deterministic","masking_policy":"strict","selection_mode":"selected","selected_categories":["content","um"]},
      "created_at":"2026-07-18T12:00:00+07:00",
      "expires_at":"2026-07-19T12:30:00+07:00",
      "retention_pinned_by_export_count":1
    }
  ],
  "page": {"limit":100,"returned":1,"next_cursor":null}
}
```

Return safe descriptors only. Never return connection details, raw SQL, samples, or secret metadata. Compute `request_fingerprint` from canonical JSON of the normalized non-secret create request, excluding the idempotency key and transient fields. Compute `selection_fingerprint` whenever effective selection changes. A fresh session may resume only when all applicable fingerprints exactly match its normalized current intent; profile/status similarity alone is never sufficient.

### 11.6 Get catalog status

```http
GET /api/v1/catalogs/{catalog_id}
```

Response while waiting for the user:

```json
{
  "catalog_id": "cat_01J...",
  "request_fingerprint": "sha256:catalog-request-...",
  "selection_fingerprint": "sha256:selection-ask-...",
  "cross_process_resumable": true,
  "status": "awaiting_selection",
  "progress": {
    "phase": "preliminary_classification",
    "discovered": 842,
    "preliminary_classified": 805,
    "preliminary_unresolved": 37,
    "fully_analyzed": 0
  },
  "category_preview_url": "/api/v1/catalogs/cat_01J.../category-preview"
}
```

Response after final classification:

```json
{
  "catalog_id": "cat_01J...",
  "request_fingerprint": "sha256:catalog-request-...",
  "selection_fingerprint": "sha256:selection-selected-content-um-...",
  "cross_process_resumable": true,
  "status": "ready",
  "progress": {
    "phase": "final_classification",
    "discovered": 842,
    "fully_analyzed": 838,
    "analysis_failed": 4,
    "final_classified": 826,
    "final_unresolved": 12,
    "materialization_included": 214,
    "materialization_excluded": 624
  },
  "sitemap_url": "/api/v1/catalogs/cat_01J.../sitemap",
  "materialization_plan_url": "/api/v1/catalogs/cat_01J.../materialization-plan"
}
```

Statuses:

```text
queued
discovering_names
preliminary_classifying
awaiting_selection
extracting_all_objects
analyzing_relationships
final_classifying
awaiting_resolution
ready
partial
failed
cancelled
```

### 11.6a Cancel or delete a catalog job

```http
POST   /api/v1/catalogs/{catalog_id}/cancel
DELETE /api/v1/catalogs/{catalog_id}
```

Cancellation is cooperative and idempotent. The application stops scheduling new work, propagates cancellation to an active adapter query when supported, reaches `cancelled`, and retains an honest partial report. Repeated cancellation returns the same terminal state. Delete requires the Section 12.4 owner approval, returns `200 DeleteResult`, and is allowed only when the catalog and dependent exports are not active; otherwise return `409 JOB_ACTIVE`.

### 11.7 Get preliminary category preview

```http
GET /api/v1/catalogs/{catalog_id}/category-preview?limit=100&cursor=...
```

The response uses the category-preview shape defined in Section 10.4.

The endpoint must clearly state that results are preliminary.

### 11.8 Set materialization selection

```http
POST /api/v1/catalogs/{catalog_id}/selection
```

Input for all categories:

```json
{
  "mode": "all",
  "selected_categories": []
}
```

Input for selected categories:

```json
{
  "mode": "selected",
  "selected_categories": ["um", "content"]
}
```

Response `202`:

```json
{
  "catalog_id": "cat_01J...",
  "status": "extracting_all_objects",
  "analysis_scope": {
    "object_count": 842,
    "restricted_by_selection": false
  },
  "selection_intent": {
    "mode": "selected",
    "selected_categories": ["um", "content"]
  }
}
```

The selected categories must not be converted into database include/exclude filters.

### 11.9 Get paginated sitemap

```http
GET /api/v1/catalogs/{catalog_id}/sitemap?view=analysis&limit=100&cursor=...
GET /api/v1/catalogs/{catalog_id}/sitemap?view=materialization&limit=100&cursor=...
```

Use the response rules defined in Section 10.

### 11.10 Get materialization plan

```http
GET /api/v1/catalogs/{catalog_id}/materialization-plan
```

Response:

```json
{
  "selection": {
    "mode": "selected",
    "selected_categories": ["um", "content"]
  },
  "counts": {
    "discovered": 842,
    "fully_analyzed": 838,
    "analysis_failed": 4,
    "included": 214,
    "intentionally_excluded": 624
  },
  "classification_changes": {
    "moved_into_selected_categories": 11,
    "moved_out_of_selected_categories": 7
  },
  "boundary_relationships": 93,
  "unresolved_affecting_selection": 3
}
```

### 11.11 Get unresolved classifications

```http
GET /api/v1/catalogs/{catalog_id}/classification-requests
```

Response:

```json
{
  "catalog_id": "cat_01J...",
  "existing_categories": ["um", "content"],
  "suggested_new_categories": ["audit"],
  "items": [
    {
      "object_id": "table:agrimap_app.APP_AUDIT_LOG",
      "evidence": [
        "name token AUDIT",
        "foreign key to UM_USER"
      ],
      "candidates": [
        {"category":"audit","confidence":0.78},
        {"category":"um","confidence":0.42}
      ]
    }
  ],
  "page": {"limit":100,"returned":1,"next_cursor":null}
}
```

The Skill must request pages until `page.next_cursor` is `null`.

### 11.12 Submit model proposals and resolve classifications

Before owner resolution, an active harness/model may submit optional non-authoritative Pass 2 proposals:

```http
POST /api/v1/catalogs/{catalog_id}/classification-proposals
Content-Type: application/json
```

Input:

```json
{
  "proposer": {
    "harness": "codex",
    "skill_version": "1.1.0"
  },
  "proposals": [
    {
      "object_id": "table:agrimap_app.APP_AUDIT_LOG",
      "category": "audit",
      "confidence": 0.78,
      "evidence_ids": ["ev_name_audit", "ev_fk_um_user"],
      "rationale": "Name and sanitized relationship evidence indicate an audit domain."
    }
  ]
}
```

Response `200`:

```json
{
  "accepted_as_suggestion": 1,
  "rejected": 0,
  "requires_owner_resolution": 1
}
```

Rules:

- `harness` is one of `codex`, `claude`, `gemini`, or `other` and is provenance, not authority.
- Every `evidence_id` must reference sanitized evidence already present in the catalog.
- The server rejects invented object IDs, category IDs, or evidence IDs.
- A model proposal may produce only `final_suggested` or `final_unresolved`; it must never produce `final_confirmed`.
- The endpoint must not accept raw SQL, sample values, credentials, or an `owner=true` assertion.

Owner resolution remains authoritative:

#### Resolve classifications

This authoritative state change requires the Section 12.4 request-bound owner approval. An agent token may submit suggestions but cannot confirm a resolution by itself.

```http
POST /api/v1/catalogs/{catalog_id}/classification-resolutions
```

Input:

```json
{
  "resolutions": [
    {
      "object_id": "table:agrimap_app.APP_AUDIT_LOG",
      "category": "audit"
    }
  ],
  "persist_as_owner_override": true
}
```

Response `200`:

```json
{
  "resolved": 1,
  "remaining": 0
}
```

### 11.13 Create export batch

```http
POST /api/v1/exports
Idempotency-Key: <required-client-generated-non-secret-key>
```

Input:

```json
{
  "catalog_id": "cat_01J...",
  "object_ids": [
    "table:agrimap_app.UM_USER",
    "procedure:agrimap_app.UM_USER_I"
  ]
}
```

Version 1 exposes no Boolean switch that can disable SQLFluff or sample appending. The application always formats final-materialization SQL through the export-pinned host-interpreter/tooling fingerprint and appends sanitized samples/shortage metadata according to catalog policy. A compatibility client that sends `sqlfluff:false` or `append_samples:false` receives `400 MANDATORY_EXPORT_STAGE_DISABLED`; exclude rules come from the versioned server policy, not arbitrary export input.

The export uses the same required idempotency semantics as catalog creation. A repeated key plus identical normalized request returns the existing export; a different request returns `409 IDEMPOTENCY_CONFLICT`.

Response `202`:

```json
{
  "export_id": "exp_01J...",
  "request_fingerprint": "sha256:export-request-...",
  "python_executable_fingerprint": "sha256:host-python-...",
  "python_version": "3.11+",
  "sqlfluff_version": "PINNED_VERSION",
  "tooling_fingerprint": "sha256:tooling-...",
  "output_format_version": "1",
  "status": "queued",
  "status_url": "/api/v1/exports/exp_01J..."
}
```

### 11.13a Rediscover export jobs

```http
GET /api/v1/exports?catalog_id=cat_01J...&status=completed_with_warnings&limit=100&cursor=...
```

Response:

```json
{
  "items": [
    {"export_id":"exp_01J...","catalog_id":"cat_01J...","status":"completed_with_warnings","request_fingerprint":"sha256:export-request-...","object_batch_fingerprint":"sha256:ordered-object-ids-...","python_executable_fingerprint":"sha256:host-python-...","python_version":"3.11+","sqlfluff_version":"PINNED_VERSION","tooling_fingerprint":"sha256:tooling-...","size_bytes":481002,"sha256":"sha256:bundle-...","manifest_sha256":"sha256:manifest-...","output_format_version":"1","expires_at":"2026-07-19T12:30:00+07:00"}
  ],
  "page": {"limit":100,"returned":1,"next_cursor":null}
}
```

The export `request_fingerprint` covers canonical catalog ID, ordered object-batch fingerprint, mandatory format/sample policy versions, the host-interpreter and tooling fingerprints, and output format version. Resume only an exact match.

### 11.14 Get export status

```http
GET /api/v1/exports/{export_id}
```

Response:

```json
{
  "export_id": "exp_01J...",
  "status": "completed_with_warnings",
  "request_fingerprint": "sha256:export-request-...",
  "python_executable_fingerprint": "sha256:host-python-...",
  "python_version": "3.11+",
  "sqlfluff_version": "PINNED_VERSION",
  "tooling_fingerprint": "sha256:tooling-...",
  "output_format_version": "1",
  "size_bytes": 481002,
  "sha256": "sha256:bundle-...",
  "manifest_sha256": "sha256:manifest-...",
  "objects": {
    "requested": 2,
    "succeeded": 2,
    "parse_failed": 1,
    "failed": 0
  },
  "artifacts": {
    "bundle_url": "/api/v1/exports/exp_01J.../bundle",
    "manifest_url": "/api/v1/exports/exp_01J.../manifest",
    "report_url": "/api/v1/exports/exp_01J.../report"
  }
}
```

For a completed status, `size_bytes`, bundle `sha256`, `manifest_sha256`, `python_executable_fingerprint`, `python_version`, `sqlfluff_version`, `tooling_fingerprint`, and `output_format_version` are immutable. The absolute interpreter path is protected state and must not appear in public responses. HTTP and `sqlctx_get_export_status` return the same normalized values. `sqlctx export fetch` requires the integrity fields before download, enforces the declared size while streaming, and rechecks both bundle and manifest hashes afterward.

### 11.14a Cancel or delete an export job

```http
POST   /api/v1/exports/{export_id}/cancel
DELETE /api/v1/exports/{export_id}
```

Cancellation and deletion follow the catalog rules: cooperative/idempotent cancel, request-bound owner approval for delete, no silent removal of active or unexpired artifacts, and `409 JOB_ACTIVE` when immediate deletion is unsafe.

### 11.15 Download batch bundle

```http
GET /api/v1/exports/{export_id}/bundle
```

Response:

```text
200 application/zip
```

This binary endpoint is consumed by the deterministic helper only:

```bash
sqlctx export fetch --export-id exp_01J... --destination <os-temp-path>
```

The CLI reads authentication from protected owner-approved connection metadata inside its process; the token is never present in the prompt or command line. Before assembly, it verifies declared size, bundle hash, manifest hashes, and path safety in an OS temporary directory. MCP must not return ZIP/base64 content or an unrestricted local runtime path.

Bundle filename:

```text
exp_01J....sqlctx.zip
```

### 11.16 Get manifest

```http
GET /api/v1/exports/{export_id}/manifest
```

Response `200 application/json`.

### 11.16a Get structured export report

```http
GET /api/v1/exports/{export_id}/report
```

Response `200 application/json` using the same `ExportReport` model as `sqlctx://export/{export_id}/report`.

### 11.17 Validate assembled output

After assembly, run the deterministic local validator:

```bash
sqlctx validate output --root <selected-output-root>
```

It must reopen every managed destination file, compute relative path, byte length, and SHA-256, verify the managed manifest itself, and build a canonical inventory. The root path remains local and is never sent to the server or model.

```http
POST /api/v1/validations
```

Input:

```json
{
  "catalog_id": "cat_01J...",
  "export_ids": ["exp_1"],
  "expected_discovered_count": 1,
  "expected_analyzed_count": 1,
  "expected_materialized_count": 1,
  "expected_output_format_version": "1",
  "assembled_inventory": {
    "managed_manifest_sha256": "sha256:managed-manifest-...",
    "inventory_sha256": "sha256:canonical-inventory-...",
    "files": [
      {"path":"manifest.yaml","size_bytes":2048,"sha256":"sha256:..."},
      {"path":"um/tables/UM_USER.sql","size_bytes":8192,"sha256":"sha256:..."}
    ]
  }
}
```

`assembled_inventory.files` must contain every path declared by the managed-file manifest; the abbreviated fixture above has two managed files. The server compares the submitted inventory with the union of immutable export manifests. Inventory size is bounded (default 2 MiB for the v1 scale); a missing, extra, duplicated, moved, size-mismatched, or hash-mismatched managed file makes validation fail. An unrestricted destination path is not part of the API/MCP model.

Response:

```json
{
  "valid": true,
  "checks": {
    "analysis_scope_accounted_for": true,
    "all_materialization_objects_exported": true,
    "intentional_exclusions_accounted_for": true,
    "no_duplicate_paths": true,
    "no_raw_secrets_detected": true,
    "boundary_relationships_recorded": true,
    "manifest_hashes_valid": true,
    "assembled_inventory_complete": true,
    "assembled_files_match_export_manifests": true
  }
}
```

### 11.18 SQLFluff endpoints

```http
GET  /api/v1/tooling/sqlfluff
POST /api/v1/tooling/sqlfluff/ensure
POST /api/v1/tooling/sqlfluff/update
```

Update input:

```json
{
  "version": "latest-stable"
}
```

Status/ensure/update responses expose safe host-tooling identity, never the absolute interpreter path:

```json
{
  "python_executable_fingerprint": "sha256:host-python-...",
  "python_version": "3.11+",
  "environment_owner": "host",
  "sqlfluff_version": "PINNED_VERSION",
  "tooling_fingerprint": "sha256:tooling-...",
  "ready": true,
  "update_blocked_by_active_jobs": false
}
```

`GET` has no installation side effect. `ensure` reuses the exact selected host interpreter and, only when a supported base interpreter is missing the pinned package, requires the Section 12.4 request-bound owner approval before the user-site install. `update` also requires that approval, returns `409 TOOLING_BUSY` while export/format work is active, and updates/rolls back through the same interpreter. An agent bearer alone cannot authorize either package mutation. These operations must never create or manage a Python environment.

### 11.19 HTTP status behavior

| Status | Meaning |
|---|---|
| `200` | Synchronous success |
| `202` | Job accepted |
| `400` | Invalid input |
| `401` | Missing/invalid API authentication |
| `403` | Profile/policy denial or privileged operation awaiting a matching owner approval |
| `404` | Unknown profile/catalog/export/object |
| `409` | Invalid job state, conflicting resolution, or `TOOLING_BUSY` while formatting/export work is active |
| `413` | Batch or response exceeds configured limit |
| `422` | Unsupported engine/object/dialect/capability |
| `429` | Rate limit or concurrency limit |
| `500` | Sanitized internal error |
| `503` | Database/tooling temporarily unavailable |
| `507` | Runtime storage quota exhausted after expired-artifact cleanup |

Error shape:

```json
{
  "error": {
    "code": "SQLFLUFF_PARSE_FAILED",
    "message": "One SQL object could not be parsed; original cleaned SQL was preserved.",
    "retryable": false,
    "correlation_id": "corr_01J..."
  }
}
```

Privileged-operation response `403`:

```json
{
  "error": {
    "code": "APPROVAL_REQUIRED",
    "message": "This operation requires an interactive owner approval.",
    "retryable": true,
    "correlation_id": "corr_01J...",
    "approval": {
      "challenge_id": "apr_01J...",
      "request_digest": "sha256:normalized-request-...",
      "operation": "sqlctx_delete_export",
      "target": "exp_01J...",
      "expires_at": "2026-07-18T12:05:00+07:00"
    }
  }
}
```

Never include raw database errors if they contain SQL text or values.

### 11.20 Contract completeness and generated API reference

The implementation must use one typed request/response model for HTTP and MCP. OpenAPI and MCP JSON Schemas must be generated from these shared models and checked for semantic equivalence in contract tests.

Every operation must document:

- function/operation name,
- HTTP method and complete relative URL when applicable,
- authentication/authorization requirement,
- path, query, and body input schema,
- success output schema and example,
- all expected error codes,
- synchronous/job behavior,
- idempotency and safe-retry behavior,
- pagination termination rule where applicable.

Minimum HTTP contract map:

| HTTP operation | Input model | Success output | Behavior |
|---|---|---|---|
| `GET /health` | none | `HealthResponse` | synchronous; no database probe |
| `GET /capabilities` | none | `CapabilitiesResponse` | synchronous; safe to retry |
| `GET /profiles` | none | `ProfileDescriptorList` | synchronous; never returns secrets |
| `POST /profiles/{profile}/test` | `profile` path | `ConnectionTestResult` | bounded read-only test |
| `GET /catalogs` | cursor/filter query | `CatalogJobPage` | fingerprinted safe rediscovery; paginated |
| `POST /catalogs` | `CreateCatalogRequest` + required `Idempotency-Key` header | `CatalogAccepted` | asynchronous `202`; same key/request returns same job |
| `GET /catalogs/{catalog_id}` | `catalog_id` path | `CatalogStatus` | pollable; safe to retry |
| `POST /catalogs/{catalog_id}/cancel` | path only | `CatalogStatus` | cooperative and idempotent |
| `DELETE /catalogs/{catalog_id}` | path only | `DeleteResult` | request-bound owner approval; rejects active dependent work |
| `GET /catalogs/{catalog_id}/category-preview` | cursor/limit query | `CategoryPreviewPage` | preliminary, paginated |
| `POST /catalogs/{catalog_id}/selection` | `MaterializationSelection` | `CatalogStatus` | state transition; idempotent for identical input |
| `GET /catalogs/{catalog_id}/sitemap` | view/cursor/limit | `SitemapPage` | paginated until `next_cursor=null` |
| `GET /catalogs/{catalog_id}/materialization-plan` | path only | `MaterializationPlan` | final-category view |
| `GET /catalogs/{catalog_id}/classification-requests` | cursor/limit query | `ClassificationRequestPage` | sanitized evidence only |
| `POST /catalogs/{catalog_id}/classification-proposals` | `ClassificationProposalBatch` | `ProposalBatchResult` | non-authoritative suggestions |
| `POST /catalogs/{catalog_id}/classification-resolutions` | `ClassificationResolutionBatch` | `ResolutionBatchResult` | request-bound owner approval for authoritative state change |
| `GET /exports` | cursor/filter query | `ExportJobPage` | request/batch-fingerprinted safe rediscovery; paginated |
| `POST /exports` | `ExportBatchRequest` + required `Idempotency-Key` header | `ExportAccepted` | asynchronous `202`; same key/request returns same job |
| `GET /exports/{export_id}` | path only | `ExportStatus` | pollable; completed state includes immutable size/hashes/tooling fingerprint/format version |
| `POST /exports/{export_id}/cancel` | path only | `ExportStatus` | cooperative and idempotent |
| `DELETE /exports/{export_id}` | path only | `DeleteResult` | request-bound owner approval for immediate cleanup |
| `GET /exports/{export_id}/bundle` | path only | ZIP bytes | consumed only by `sqlctx export fetch`; validate size/hash/manifest/path safety |
| `GET /exports/{export_id}/manifest` | path only | `ExportManifest` | structured manifest |
| `GET /exports/{export_id}/report` | path only | `ExportReport` | same structured report as MCP resource |
| `POST /validations` | `ValidationRequest` with complete `AssembledInventory` | `ValidationResult` | compares destination rehash inventory with export manifests and blocks completion on mismatch |
| `GET /tooling/sqlfluff` | none | `SqlFluffStatus` | synchronous |
| `POST /tooling/sqlfluff/ensure` | `SqlFluffEnsureRequest` | `SqlFluffStatus` | same host interpreter; request-bound owner approval before a missing-package user-site install; never creates an environment |
| `POST /tooling/sqlfluff/update` | `SqlFluffUpdateRequest` | `SqlFluffUpdateResult` | request-bound owner approval; idle-only same-interpreter update/rollback |

The generated `docs/api-and-mcp-examples.md` must include at least one success and one error example for every operation family. Do not hand-maintain a second schema that can drift from runtime models.

---

## 12. MCP Specification

MCP tools must mirror application commands, not raw HTTP mechanics.

### 12.1 Tools

```text
sqlctx_get_capabilities
sqlctx_list_profiles
sqlctx_test_profile
sqlctx_list_catalogs
sqlctx_create_catalog
sqlctx_get_catalog_status
sqlctx_cancel_catalog
sqlctx_delete_catalog
sqlctx_get_category_preview
sqlctx_set_materialization_selection
sqlctx_list_sitemap
sqlctx_get_materialization_plan
sqlctx_get_classification_requests
sqlctx_submit_classification_proposals
sqlctx_resolve_classifications
sqlctx_list_exports
sqlctx_export_batch
sqlctx_get_export_status
sqlctx_cancel_export
sqlctx_delete_export
sqlctx_validate_exports
sqlctx_sqlfluff_status
sqlctx_sqlfluff_ensure
sqlctx_sqlfluff_update
```

### 12.2 Resources

```text
sqlctx://export/{export_id}/manifest
sqlctx://export/{export_id}/report
```

Paginated catalog data is available only through `sqlctx_get_category_preview`, `sqlctx_list_sitemap`, `sqlctx_get_materialization_plan`, and `sqlctx_get_classification_requests`, whose input schemas carry cursor/limit and sitemap view. Do not expose duplicate catalog resources without those parameters. Large bundle bytes are also not an MCP resource. The sole v1 transfer path is the HTTP binary endpoint through `sqlctx export fetch`; MCP exposes only safe status, size, hash, manifest, and report metadata.

Resource parity tests are mandatory:

- `GET /api/v1/exports/{export_id}/manifest` and `sqlctx://export/{export_id}/manifest` return the same normalized `ExportManifest`.
- `GET /api/v1/exports/{export_id}/report` and `sqlctx://export/{export_id}/report` return the same normalized `ExportReport`.
- HTTP health and HTTP binary download are transport-specific exceptions; neither creates a fake MCP large-content operation.

### 12.3 MCP tool behavior

Each tool must:

- define strict JSON Schema input,
- define structured output,
- return sanitized errors,
- be idempotent where possible,
- support cursor pagination where a list can grow,
- expose a human-readable summary and structured content,
- never accept credentials,
- never accept arbitrary SQL,
- never accept an unrestricted filesystem path.

### 12.4 Human control

Require explicit user confirmation for:

- installing SQLFluff when it requires network access,
- updating SQLFluff,
- creating a new persistent category override,
- enabling remote server access,
- changing masking policy from strict to a weaker policy.

Normal read-only catalog and export calls may be automated after the owner has started and authorized the server.

Human confirmation must be server-enforced, not inferred from chat:

1. The harness authenticates with an `agent`-scoped token. That token alone cannot delete jobs, persist classification resolutions/overrides, install or update SQLFluff, enable remote access, or weaken masking.
2. An attempted privileged HTTP/MCP operation returns `403 APPROVAL_REQUIRED` with a non-secret `challenge_id`, normalized `request_digest`, operation, target, and expiry.
3. The owner runs `sqlctx approvals grant --challenge <challenge_id>` locally in an interactive terminal and completes the configured owner-presence check. The CLI unlocks the separate owner credential internally; no credential appears in the command line or model context, and non-interactive grant attempts fail closed.
4. The server binds the approval to authenticated agent caller, exact operation, target, and normalized request digest. Approval expires after five minutes by default and is single-use.
5. The agent retries the identical request; the server consumes the matching approval. Any changed field, caller, target, operation, expired approval, or replay returns `403 APPROVAL_REQUIRED`.
6. The owner may execute the privileged operation directly through the local control CLI instead. All grants, denials, consumption, expiry, and privileged results are audited without secrets.

### 12.5 MCP transport and complete tool contracts

Default MCP endpoint:

```text
Transport: Streamable HTTP
URL: http://127.0.0.1:<owner-selected-port>/mcp
Lifecycle: owner starts sqlctx-server before the harness connects
Authentication: agent-scoped bearer supplied by harness configuration; privileged operations additionally require a server-side owner grant, never a credential tool argument
```

MCP tools reuse the shared models from Section 11.20:

| MCP tool | Input model | Structured output | Important behavior |
|---|---|---|---|
| `sqlctx_get_capabilities` | empty object | `CapabilitiesResponse` | no database probe |
| `sqlctx_list_profiles` | empty object | `ProfileDescriptorList` | safe descriptors only |
| `sqlctx_test_profile` | `TestProfileRequest` | `ConnectionTestResult` | bounded read-only test |
| `sqlctx_list_catalogs` | `JobCursorRequest` | `CatalogJobPage` | fingerprinted rediscovery; cursor pagination |
| `sqlctx_create_catalog` | `CreateCatalogRequest` with required `idempotency_key` | `CatalogAccepted` | asynchronous; shared application idempotency semantics |
| `sqlctx_get_catalog_status` | `CatalogIdRequest` | `CatalogStatus` | pollable |
| `sqlctx_cancel_catalog` | `CatalogIdRequest` | `CatalogStatus` | cooperative and idempotent |
| `sqlctx_delete_catalog` | `CatalogIdRequest` | `DeleteResult` | request-bound owner approval; rejects active dependent work |
| `sqlctx_get_category_preview` | `CatalogCursorRequest` | `CategoryPreviewPage` | preliminary; cursor pagination |
| `sqlctx_set_materialization_selection` | `CatalogSelectionRequest` | `CatalogStatus` | selection never narrows analysis |
| `sqlctx_list_sitemap` | `SitemapRequest` | `SitemapPage` | stop only at null cursor |
| `sqlctx_get_materialization_plan` | `CatalogIdRequest` | `MaterializationPlan` | final categories only |
| `sqlctx_get_classification_requests` | `CatalogCursorRequest` | `ClassificationRequestPage` | sanitized evidence only |
| `sqlctx_submit_classification_proposals` | `ClassificationProposalBatch` | `ProposalBatchResult` | model suggestion, never owner confirmation |
| `sqlctx_resolve_classifications` | `ClassificationResolutionBatch` | `ResolutionBatchResult` | request-bound owner approval for persistent resolution/override |
| `sqlctx_list_exports` | `ExportJobCursorRequest` | `ExportJobPage` | request/batch-fingerprinted rediscovery; cursor pagination |
| `sqlctx_export_batch` | `ExportBatchRequest` with required `idempotency_key` | `ExportAccepted` | bounded, resumable, idempotent batch |
| `sqlctx_get_export_status` | `ExportIdRequest` | `ExportStatus` | returns immutable size/hashes/tooling fingerprint/format version and resource links, not large content |
| `sqlctx_cancel_export` | `ExportIdRequest` | `ExportStatus` | cooperative and idempotent |
| `sqlctx_delete_export` | `ExportIdRequest` | `DeleteResult` | request-bound owner approval for immediate cleanup |
| `sqlctx_validate_exports` | `ValidationRequest` with complete `AssembledInventory` | `ValidationResult` | compares destination rehash inventory and blocks false completion |
| `sqlctx_sqlfluff_status` | empty object | `SqlFluffStatus` | safe fingerprints/versions only; no install side effect |
| `sqlctx_sqlfluff_ensure` | `SqlFluffEnsureRequest` | `SqlFluffStatus` | same host interpreter; request-bound owner approval before a missing-package user-site install; never creates an environment |
| `sqlctx_sqlfluff_update` | `SqlFluffUpdateRequest` | `SqlFluffUpdateResult` | request-bound owner approval; idle-only same-interpreter update/rollback |

Representative paginated input and output:

```json
{
  "tool": "sqlctx_get_category_preview",
  "arguments": {
    "catalog_id": "cat_01J...",
    "cursor": null,
    "limit": 100
  }
}
```

```json
{
  "catalog_id": "cat_01J...",
  "classification_pass": "preliminary",
  "analysis_scope": {"discovered_objects": 842},
  "items": [
    {"category": "um", "preliminary_count": 64, "examples": ["UM_USER", "UM_ROLE"], "confidence_summary": {"confirmed_by_rule": 58, "suggested": 6}}
  ],
  "unresolved_count": 37,
  "page": {"limit": 100, "returned": 1, "next_cursor": null},
  "warning": "Preliminary categories may change after full relationship analysis."
}
```

Representative model proposal input and output:

```json
{
  "tool": "sqlctx_submit_classification_proposals",
  "arguments": {
    "catalog_id": "cat_01J...",
    "proposer": {"harness": "gemini", "skill_version": "1.1.0"},
    "proposals": [
      {
        "object_id": "table:agrimap_app.APP_OBJECT",
        "category": "content",
        "confidence": 0.84,
        "evidence_ids": ["ev_fk_content", "ev_written_by_content_i"]
      }
    ]
  }
}
```

```json
{
  "accepted_as_suggestion": 1,
  "rejected": 0,
  "requires_owner_resolution": 1
}
```

For every tool, generate and publish an example for success, validation failure, state conflict, and dependency failure when those cases apply. Contract tests must call the same scenario through HTTP and MCP and compare normalized structured results.

For create operations, the HTTP `Idempotency-Key` header and MCP `idempotency_key` field map to the same application command property. A key is scoped to caller plus operation; the same normalized request returns the original job, while a different request returns `409 IDEMPOTENCY_CONFLICT` (or the equivalent structured MCP error).

---

## 13. Two-pass Category Classification and User Selection

### 13.1 Mandatory two-pass invariant

Classification must run in two distinct passes.

```text
Pass 1:
  Preliminary category discovery from safe names and lightweight metadata.

Pass 2:
  Final classification from the complete sanitized object and relationship context.
```

The system must not collapse both passes into one model prompt or one opaque score.

The owner selects categories after seeing Pass 1, but that selection controls only final materialization.

```text
selected categories != database extraction filter
```

Every permitted object must still be extracted and analyzed before final output filtering.

### 13.2 Pass 1 — Preliminary category discovery

Allowed evidence:

1. Owner category rules
2. Exact object-name match
3. Prefix/token match
4. Schema name
5. Object type
6. Lightweight database comment/description

Pass 1 output:

```text
preliminary_confirmed:
  deterministic owner/config rule matched

preliminary_suggested:
  name/schema evidence suggests a category

preliminary_unresolved:
  no category or conflicting name evidence
```

Even `preliminary_confirmed` may be challenged by Pass 2 relationship evidence. The final report must record any change.

Pass 1 must return enough information for a user to choose categories:

- category name,
- description,
- object count,
- representative object names,
- preliminary confidence summary,
- unresolved count,
- explicit warning that classification may change.

### 13.3 User selection behavior

When the current user request does not explicitly say “all” or name selected categories, use `ask` mode.

Ask one concise question:

```text
ต้องการสร้างทุกหมวด หรือเลือกเฉพาะบางหมวด?
```

("Build all categories, or select only some?" — ask in the owner's language.)

Present:

- `ทั้งหมด`
- every preliminary category,
- an option to select multiple categories,
- the unresolved object count,
- a warning that the server will still analyze all database objects.

Do not ask the user to choose individual objects at this stage.

Selection rules:

- `all` means materialize every final category.
- `selected` stores category names, not Pass 1 object IDs.
- If a selected category disappears or is renamed after Pass 2, stop materialization and ask the owner.
- If Pass 2 creates a new category closely connected to selected categories, include it in the materialization-plan warning but do not silently select it.

### 13.4 Full analysis before final classification

After selection is recorded, extract every permitted object and build:

- complete table metadata,
- masked sample rows,
- primary/unique/foreign-key relationships,
- procedure definitions,
- procedure read/write/call relationships,
- object descriptions,
- incoming and outgoing dependency neighborhoods.

Full analysis must not be skipped for objects outside preliminary selected categories.

This is necessary because:

- names can be ambiguous,
- shared tables can belong to multiple domains,
- procedures can reveal hidden table ownership,
- foreign-key neighborhoods can place generic names into the correct business context.

### 13.5 Pass 2 — Final evidence order

Use evidence in this priority:

1. Owner override
2. Exact configured object match
3. Configured prefix
4. Database schema ownership
5. Database comments/extended properties
6. Column names, types, and key roles
7. Foreign-key neighborhood
8. Routine read/write/call dependencies
9. Sanitized sample shape and representative values
10. Name-token similarity
11. Sanitized semantic proposal submitted by the active harness/model, when available

A semantic or model-generated suggestion alone must never become `final_confirmed`. The server must not call a provider API to obtain it.

Final statuses:

```text
final_confirmed:
  owner override or deterministic rule supported by contextual evidence

final_suggested:
  strong contextual evidence but no owner confirmation

final_unresolved:
  conflicting evidence, no suitable category, or low confidence
```

### 13.6 Category movement rules

The system must compare Pass 1 and Pass 2.

For every object, record:

```json
{
  "object_id": "table:agrimap_app.APP_OBJECT",
  "preliminary_category": null,
  "final_category": "content",
  "changed": true,
  "change_reason": [
    "foreign key to CONTENT",
    "written by CONTENT_I"
  ]
}
```

Materialization uses `final_category`.

Therefore:

- objects moved into a selected category are included,
- objects moved out of a selected category are excluded in strict selected mode,
- excluded related objects remain visible as boundary nodes and edges,
- materialization decisions must be explainable.

### 13.7 Unknown handling after Pass 2

The skill must not guess.

In `all` mode, ask for every unresolved object.

In `selected` mode, prioritize unresolved objects that:

- may belong to a selected category,
- are direct dependencies of selected objects,
- bridge two selected categories,
- would materially change selected-output dependency interpretation,
- have conflicting high-confidence evidence.

Other unresolved objects may remain excluded under `unresolved`, but must still appear in analysis reports.

The consolidated owner question must contain:

- every current final category,
- suggested new categories,
- unresolved objects,
- preliminary and final candidates,
- confidence,
- relationship evidence,
- whether the object affects selected output,
- the option to leave it unresolved.

Example:

```text
เลือก Output: um, content

พบ 3 objects ที่อาจกระทบ Output:
- APP_OBJECT
  - preliminary: unresolved
  - final candidate: content
  - evidence: FK -> CONTENT, written by CONTENT_I
- USER_CONTENT_MAP
  - preliminary: um
  - final candidates: um / content
  - evidence: bridge table between UM_USER and CONTENT
- GLOBAL_SETTING
  - preliminary: content
  - final candidate: platform_config
  - evidence: used only by platform configuration procedures

กรุณากำหนด category หรือเลือก unresolved
```

Do not ask one question per table when a consolidated decision is possible.

### 13.8 Persisted decisions

Owner resolutions must be stored separately from generated artifacts:

```text
config/category-overrides.yaml
```

A classification rerun must not require a new database dump when:

- the catalog snapshot is still valid,
- the masking policy is unchanged,
- relationship metadata is complete,
- the source fingerprints have not changed.

### 13.9 Boundary relationship metadata

Selective output must not pretend excluded related objects do not exist.

Under the fixed v1 `index_only` boundary behavior, write boundary nodes such as:

```json
{
  "node_id": "table:agrimap_app.GLOBAL_SETTING",
  "materialized": false,
  "boundary": true,
  "final_category": "platform_config",
  "exclusion_reason": "category_not_selected"
}
```

Edges from selected objects to boundary nodes must remain in graph indexes.

SQL definitions and sample rows for boundary nodes must not be written in v1. Their sanitized descriptors and edges exist only in boundary/index metadata.

### 13.10 Harness/model proposal stage

After the server has built deterministic Pass 2 evidence and before the owner is asked to resolve ambiguity:

1. The Skill fetches all paginated classification requests.
2. The active model reviews only sanitized names, metadata, evidence IDs, relationship summaries, and masked sample shapes.
3. The Skill submits zero or more proposals through `sqlctx_submit_classification_proposals`.
4. The server validates object IDs, category IDs, evidence IDs, confidence range, and proposer provenance.
5. The server recalculates suggestions without elevating a model proposal to owner authority.
6. The Skill fetches the remaining unresolved decisions and asks one consolidated owner question.

If the harness cannot or does not provide a semantic proposal, skip steps 2–4 and continue with deterministic evidence. Cross-harness support must not depend on a particular provider model name or model version.

## 14. Relationship and Graph-ready Metadata

### 14.1 Node types

Version 1:

```text
database
schema
category
table
procedure
column
```

Future-compatible:

```text
view
function
trigger
sequence
package
materialized_view
```

### 14.2 Edge types

```text
CONTAINS
BELONGS_TO_CATEGORY
HAS_COLUMN
PRIMARY_KEY
FOREIGN_KEY
READS_FROM
WRITES_TO
CALLS
REFERENCES
DERIVED_FROM
```

### 14.3 Cardinality truth rules

Use explicit database constraints first.

Infer:

- `1:1` only when the foreign-key columns are unique on the child side.
- `1:N` when the child foreign key is not unique.
- optionality from nullability where possible.
- `M:N` only when an associative table has suitable foreign keys and a unique/composite key supporting that interpretation.

If constraints do not prove cardinality:

```json
{
  "cardinality": "unknown",
  "confidence": "inferred",
  "evidence": ["name similarity only"]
}
```

Never present an inferred relationship as a confirmed database fact.

### 14.4 Routine dependency analysis

Use this order:

1. Native database dependency catalog.
2. Parsed SQL references.
3. Conservative lexical fallback.
4. Unresolved dynamic-SQL marker.

Differentiate:

```text
READS_FROM
WRITES_TO
CALLS
UNKNOWN_DYNAMIC_REFERENCE
```

### 14.5 Graph output

Version 1 must emit:

```text
indexes/nodes.jsonl
indexes/edges.jsonl
indexes/relationships.json
indexes/routine-dependencies.json
indexes/graph.json
```

Phase 2 may render:

```text
graph/erd.mmd
graph/dependencies.mmd
graph/relationships.graphml
graph/tree.json
```

Graph rendering in Phase 2 must use the exported indexes and must not reconnect to the database.

---

## 15. Output Directory Resolution

### 15.1 User intent

The skill must detect explicit output intent such as:

```text
เขียนไปที่ docs/db-context        # write to ...
output ./knowledge/sql
สร้างที่ .agent/context/database   # generate under ...
```

Resolution order:

1. Explicit path in the current user request.
2. Skill configuration `default_output`.
3. Project convention discovered from existing configuration.
4. Repository root + `sql-context/`.

Do not invent an absolute path.

### 15.2 Path safety

The skill, not the server, writes into the user-selected project path.

Rules:

- normalize path,
- reject traversal outside the permitted workspace,
- do not overwrite unrelated files,
- use a manifest to identify managed files,
- update only managed files,
- obtain owner confirmation before deleting stale managed files,
- extract downloaded bundles in an OS temp directory,
- validate hashes and paths,
- atomically move final files into place,
- clean the OS temp directory in `finally`.

### 15.3 Required output layout

Example:

```text
<output-root>/
├── manifest.yaml
├── catalog.json
├── categories.yaml
├── um/
│   ├── category.yaml
│   ├── tables/
│   │   ├── UM_USER.sql
│   │   └── UM_ROLE.sql
│   └── store_procedures/
│       ├── UM_USER_I.sql
│       └── UM_USER_U.sql
├── content/
│   ├── category.yaml
│   ├── tables/
│   │   ├── CONTENT.sql
│   │   └── CONTENT_SHARE.sql
│   └── store_procedures/
│       └── CONTENT_I.sql
├── unresolved/
│   └── classification-requests.yaml
├── indexes/
│   ├── objects.jsonl
│   ├── nodes.jsonl
│   ├── edges.jsonl
│   ├── relationships.json
│   ├── routine-dependencies.json
│   ├── tags.json
│   └── graph.json
└── reports/
    ├── export-summary.md
    ├── category-preview.json
    ├── classification-report.json
    ├── materialization-plan.json
    ├── masking-report.json
    ├── sqlfluff-report.json
    └── integrity-report.json
```

Do not add an extra `categories/` parent directory; business categories must appear directly under the selected output root as requested.

### 15.4 Object file naming

```text
<table-name>.sql
<procedure-name>.sql
```

Collision handling:

- Include schema only if two objects would map to the same relative path.
- Use a deterministic suffix, not a random value.
- Record the original fully qualified name in the object index.

### 15.5 Manifest example

```yaml
output_format_version: "1"
generator:
  name: sql-context-pack
  version: "1.1.0"

source:
  profile: agrimap-readonly
  engine: sqlserver
  database_name: "[POLICY_HIDDEN]"
  schemas:
    - agrimap_app

export:
  created_at: "2026-07-18T12:00:00+07:00"
  discovered_object_count: 842
  fully_analyzed_object_count: 838
  analysis_failed_object_count: 4
  materialized_object_count: 214
  intentionally_excluded_object_count: 624
  table_count_in_materialization: 130
  procedure_count_in_materialization: 84
  requested_samples_per_table: 10

selection:
  mode: selected
  selected_categories:
    - um
    - content
  excluded_dependencies: index_only_boundary_metadata

classification:
  strategy: two_pass
  moved_into_selected_categories: 11
  moved_out_of_selected_categories: 7
  unresolved_affecting_selection: 0

security:
  masking_policy: strict
  raw_credentials_exported: false
  raw_secrets_detected_after_export: false

sqlfluff:
  format_scope: final_materialization
  python_executable_fingerprint: "sha256:host-python-..."
  python_version: "3.11+"
  version: "PINNED_VERSION"
  tooling_fingerprint: "sha256:tooling-..."
  dialect: tsql
  exclude_rules:
    - CP02
    - LT01
    - RF06
  format_requested: 214
  formatted: 210
  parse_failed_preserved: 4
  format_failed_preserved: 0
```

Manifest accounting is normative:

```text
format_requested
  = formatted
  + parse_failed_preserved
  + format_failed_preserved
  = materialized_object_count
```

The four analysis-failed objects in this example are outside materialization and therefore outside SQLFluff scope. SQLFluff must not run during full extraction/analysis.

---

## 16. Tags and Search Index

Each object index record must include:

```json
{
  "object_id": "table:agrimap_app.UM_USER",
  "qualified_name": "agrimap_app.UM_USER",
  "object_type": "table",
  "category": "um",
  "classification_status": "confirmed",
  "tags": [
    "user-management",
    "identity",
    "has-primary-key",
    "referenced-by-procedure"
  ],
  "columns": 24,
  "sample_rows": 10,
  "relationships": {
    "incoming": 4,
    "outgoing": 2
  },
  "routine_usage": {
    "read_by": 8,
    "written_by": 3
  },
  "path": "um/tables/UM_USER.sql",
  "content_hash": "sha256:..."
}
```

Tag sources must be recorded:

```json
{
  "tag": "identity",
  "source": "owner-config",
  "confidence": 1.0
}
```

Do not let generated tags overwrite owner-defined tags.

---

## 17. Performance and Resilience

### 17.1 Default limits

Suggested safe defaults:

```yaml
runtime:
  catalog_page_size: 100
  catalog_page_max: 250
  export_batch_objects: 10
  export_batch_max_objects: 25
  extraction_concurrency: 4
  sample_rows_per_table: 10
  max_sample_rows_per_table: 20
  max_text_value_chars: 512
  max_definition_bytes: 2000000
  statement_timeout_seconds: 30
  object_timeout_seconds: 60
```

Per-profile overrides may lower operational limits, but must not lower `sample_rows_per_table` below 10. Only actual source availability, access policy, masking policy, or extraction failure may produce fewer than 10 exported rows, and every shortage must be reported.

### 17.2 Bounded concurrency

- Use an async semaphore.
- Do not open one connection per table.
- Keep the database pool small.
- Allow adapter-specific concurrency limits.
- Oracle and production SQL Server profiles should default conservatively.
- Back off on transient errors.
- Never retry authentication or policy-denied errors repeatedly.

### 17.3 Resumability

Persist job checkpoints in the server runtime store, not the target project.

Checkpoint fields:

```text
catalog ID
object ID
phase
attempt count
status
sanitized error code
content hash
export ID
```

A resumed export must skip objects already completed with the same content fingerprint and policy version.

### 17.4 Partial failure

One failed object must not fail the entire database export.

Final statuses:

```text
completed
completed_with_warnings
partial
failed
cancelled
```

The final report must list:

- succeeded objects,
- parse-failed objects preserved unformatted,
- extraction failures,
- masking failures,
- unresolved categories,
- retryable failures.

### 17.5 Cache behavior

Server cache belongs in the user runtime directory, not the repository.

Cache keys must include:

- profile identifier,
- object qualified name,
- source fingerprint,
- masking policy version,
- SQLFluff version,
- host Python executable fingerprint,
- tooling fingerprint,
- formatter configuration,
- `output_format_version`.

Never cache raw unmasked sample values on disk by default.

### 17.6 Runtime retention and quota

Default runtime-store policy:

```yaml
runtime_store:
  completed_catalog_ttl_hours: 24
  completed_export_artifact_ttl_hours: 24
  max_total_bytes: 5368709120  # 5 GiB
```

- The owner may configure these values, but defaults must be explicit and observable through safe capabilities/operations output.
- Active jobs are never removed automatically.
- A catalog is dependency-pinned while any export referencing it is active or unexpired. Its effective expiry is at least the maximum expiry of all dependent exports.
- The pin covers the sanitized snapshot, checkpoints, snapshot masking key/state, alias registry, classification state, and manifests required by dependent exports.
- Before accepting a new catalog or export, remove eligible expired export artifacts first, release their catalog pins, then remove catalogs that are both expired and unpinned using an atomic, auditable cleanup process.
- Never silently remove an active job or an unexpired artifact to make space.
- If cleanup cannot provide sufficient capacity, reject the new job with `507 RUNTIME_STORAGE_FULL` and a sanitized capacity summary.
- Request-bound owner-approved delete operations provide immediate cleanup subject to active-job and dependency checks. Catalog deletion returns `409 DEPENDENT_EXPORT_EXISTS` until active/unexpired exports are deleted or expire.
- Catalog snapshots, checkpoints, bundles, reports, alias registries, and idempotency records follow the applicable job retention period.

---

## 18. Agent Skill Workflow

### 18.1 Skill objective

The skill converts a permitted database catalog into a complete, sanitized, categorized, AI-ready SQL context package in the user-selected project location.

It must distinguish:

```text
what the server analyzes
from
what the skill materializes
```

### 18.2 Selection intent detection

Before creating the catalog, parse the user request.

Examples (Thai and English inputs are equivalent):

```text
สร้างทั้งหมด          # build everything
dump ทุกหมวด           # dump every category
all categories
```

Resolve to:

```json
{"mode":"all"}
```

Examples:

```text
เอาเฉพาะ um กับ content     # only um and content
สร้างหมวด user management   # build the user management category
only content
```

Resolve to:

```json
{
  "mode":"selected",
  "selected_categories":["um","content"]
}
```

When no choice is explicit, resolve to:

```json
{"mode":"ask"}
```

Do not infer selected categories merely from the project name.

### 18.3 Mandatory workflow

```text
1. Parse the user request.
2. Resolve output directory.
3. Resolve initial materialization mode: ask, all, or selected.
4. Discover server capabilities.
5. List safe connection profiles and page through recent catalog/export jobs; resume only an exact normalized request/selection/batch fingerprint match.
6. Select only the profile explicitly named or unambiguously configured.
7. Ensure SQLFluff is available.
8. Test profile connectivity.
9. Resume the matching catalog when safe; otherwise create it with a new idempotency key and the two-pass category policy.
10. Poll until preliminary classification is available.
11. Fetch all category-preview pages using next_cursor.
12. If mode=ask:
    a. present all preliminary categories and counts;
    b. present representative names;
    c. present unresolved count;
    d. ask whether to create all categories or selected categories;
    e. store the selected category names.
13. Submit materialization selection.
14. Confirm from the response that analysis_scope.restricted_by_selection=false.
15. Poll while the server extracts every permitted object.
16. Poll through relationship analysis and final classification.
17. Fetch every analysis sitemap page until next_cursor is null.
18. Record all discovered, analyzed, failed-analysis, and unresolved object IDs.
19. Fetch the final materialization plan.
20. Inspect classification changes:
    a. moved into selected categories;
    b. moved out of selected categories;
    c. new categories connected to selected categories;
    d. boundary relationships.
21. Fetch every classification-request page until next_cursor is null.
22. Let the active harness/model optionally propose categories from sanitized evidence and submit proposals with provenance.
23. Refresh every classification-request page after proposal validation.
24. If owner decisions are required:
    a. present all final categories;
    b. present suggested categories and evidence;
    c. prioritize unresolved objects affecting selected output;
    d. ask one consolidated owner question;
    e. obtain the server-enforced request-bound owner approval and submit resolutions;
    f. refresh final classification and materialization plan.
25. Fetch every materialization sitemap page until next_cursor is null.
26. Collect only final included object IDs for export.
27. Record every intentionally excluded object and reason.
28. Partition included object IDs by recommended batch size and weight.
29. Export every materialization batch with a stable per-batch idempotency key.
30. Poll every export.
31. Fetch every completed bundle through `sqlctx export fetch`; never transfer ZIP/base64 through MCP.
32. Validate declared size, bundle/manifest hashes, and path safety in OS temp.
33. Assemble into the selected output root.
34. Run `sqlctx validate output` to reopen/hash every assembled managed file, then call final validation with:
    a. expected discovered count;
    b. expected analyzed count;
    c. expected materialized count;
    d. `expected_output_format_version`;
    e. all export IDs;
    f. the complete relative-path/size/SHA-256 assembled inventory.
35. Verify:
    discovered = analyzed + failed_analysis
    analyzed = materialized + intentionally_excluded
36. Write reports and manifest.
37. Remove all OS temporary files.
38. Report exact analysis, materialization, exclusion, warning, unresolved, and failure counts.
```

At every polling or extraction step, honor an owner/client cancellation by invoking the corresponding cooperative catalog/export cancel operation. If work is no longer needed, request-bound owner-approved delete operations may clean retained jobs explicitly.

### 18.4 Prohibited skill behavior

The skill must not:

- request database credentials from the user in chat,
- place credentials in a command line,
- print environment variables,
- call arbitrary SQL,
- use selected categories as database extraction filters,
- skip non-selected objects during full analysis,
- finalize category assignment from names alone,
- apply selection to Pass 1 object IDs instead of Pass 2 categories,
- send raw SQL or unmasked sample values to a model provider,
- represent a model proposal as an owner decision,
- silently include a newly discovered category,
- silently exclude an object moved into a selected category,
- export more sample rows than policy permits,
- request fewer than 10 sample rows per eligible table,
- weaken masking to make an export succeed,
- guess a business category,
- claim completion before analysis and materialization validation,
- stop after the first sitemap page,
- use directory-wide SQLFluff formatting,
- enable format/fix on unparsable SQL,
- create `.tmp-*` directories in the project,
- delete unmanaged project files,
- invent sample rows when a table has fewer than ten,
- silently update SQLFluff,
- hide partial failures.

### 18.5 Owner clarification format

Ask only when a real owner decision is required.

For initial selection, ask one question covering:

- all categories,
- selected categories,
- preliminary category counts,
- unresolved count.

For final classification, use one consolidated question containing only decisions that are unresolved or materially affect the selected output.

Do not ask technical questions already answerable from capabilities, manifests, the catalog, or relationship indexes.

## 19. Acceptance Criteria

### 19.1 Installation

- `pyproject.toml` declares `requires-python = ">=3.11"`; the non-Python PowerShell/POSIX preflight detects a missing or unsupported interpreter and returns `PYTHON_UNAVAILABLE` plus platform-specific owner installation guidance.
- Production selects an owner-configured absolute host-Python executable or the running process's `sys.executable`; every SQLFluff command uses that exact executable.
- On a clean supported machine with host Python but without SQLFluff, `sqlctx sqlfluff ensure` requests owner approval and installs the pinned SQLFluff version into that interpreter's user site.
- A second call does not reinstall it.
- Two concurrent ensure calls result in one installation.
- `sqlctx doctor` reports the host-Python fingerprint/version, installed SQLFluff version, and dialect availability without exposing the protected absolute path publicly.
- No command, Skill helper, plugin, server, test, or bootstrap path creates, copies, activates, or owns a virtualenv/venv, conda environment, pipx environment, or bundled Python.
- An explicitly selected pre-existing owner-managed environment is verify/execute-only; sqlctx never installs into, updates, repairs, or deletes it.
- `sqlctx sqlfluff update` changes versions through the same host interpreter only after explicit invocation and returns `409 TOOLING_BUSY` while export/format work is active.
- Failed update reinstalls and verifies the previous exact SQLFluff version through the same host interpreter or returns `TOOLING_BROKEN`; it never creates a fallback environment.
- The suite verifies that no Python-environment directory or package payload is created under `skills/`, the target project, or the sqlctx runtime store.

### 19.2 SQLFluff isolation

Given 100 SQL files where 3 are unparsable:

- 97 files are formatted.
- 3 cleaned original files are preserved.
- The process completes with warnings.
- The report identifies each failed file.
- No `fix_even_unparsable` behavior is used.
- No project-local temp directory remains.
- Every parse/format/version subprocess uses the export-pinned host-interpreter and tooling fingerprints.
- External package mutation during a job produces `TOOLING_CHANGED`; an explicit update during a job produces `409 TOOLING_BUSY`.

### 19.3 Sensitive data

Test fixtures must contain:

- national ID,
- username,
- password,
- password hash,
- JWT,
- access token,
- refresh token,
- API key,
- email,
- phone,
- hardcoded routine secret.

Acceptance:

- none of the original sensitive values appears in any API response, MCP result, bundle, report, exception, or log,
- deterministic aliases preserve required joins,
- high-risk secret classes are fully redacted,
- final post-export secret scan passes.

### 19.3a Sample-row target

- A request for 9 rows per eligible table is rejected.
- A default request against a table with at least 10 eligible rows emits exactly 10 real sanitized rows.
- An owner request for 15 within the configured maximum emits exactly 15 when available.
- A table with only 7 eligible rows emits 7 with `requested_count=10`, `actual_count=7`, and a shortage reason.
- A policy that excludes every eligible row emits zero with an explicit warning.
- No test fixture or production path duplicates or fabricates rows.

### 19.4 Pagination, full analysis, and selective completeness

Given 842 objects, page size 100, and selected categories `um` and `content`:

- the skill requests every category-preview page until `next_cursor=null`,
- all 842 unique object IDs enter `analysis_scope`,
- selected categories do not reduce extraction scope,
- every analysis sitemap page is requested,
- no object is duplicated,
- `discovered = analyzed + failed_analysis`,
- every materialization sitemap page is requested,
- `analyzed = materialized + intentionally_excluded`,
- export batches contain only final included objects,
- every excluded object has an explicit reason,
- boundary relationships are preserved,
- count mismatch causes validation failure.

In `all` mode:

- every successfully analyzed object is materialized unless security policy explicitly excludes it.

In `selected` mode:

- materialized count may be lower than discovered count,
- the difference must be intentional and fully accounted for.

### 19.5 Two-pass category handling

Given configured categories `um` and `content`:

- Pass 1 returns preliminary category counts and representative names.
- When no selection is explicit, the skill asks all versus selected categories.
- `UM_USER` preliminarily and finally resolves to `um`.
- `CONTENT_SHARE_USER` preliminarily and finally resolves to `content`.
- `APP_OBJECT` may be unresolved in Pass 1 and move to `content` after FK/procedure analysis.
- `CONTENT_CONFIG` may move out of `content` after full contextual analysis.
- Selecting `content` includes objects moved into `content`.
- Selecting `content` excludes false-positive objects moved out of `content`.
- An ambiguous `APP_AUDIT_LOG` remains unresolved.
- The skill lists current categories and candidate categories.
- No category is silently guessed.
- A harness/model proposal using valid sanitized evidence remains `final_suggested` or `final_unresolved`, never `final_confirmed`.
- A proposal with an unknown object/category/evidence ID is rejected.
- The server has no provider model API dependency or provider credential.
- Owner resolution persists as an override.
- Reclassification does not require another database extraction while the snapshot remains valid.

### 19.6 Output safety

- Explicit output path is honored.
- Traversal outside the workspace is rejected.
- Managed files are written atomically.
- Existing unmanaged files remain untouched.
- Bundle extraction rejects absolute paths and `../`.
- No `.tmp-sql-finalizer`, `.tmp-sqlfluff-format`, `.tmp-*`, or partial directory remains in the project.

### 19.7 Multi-engine mapping

Integration tests verify:

```text
sqlserver -> tsql
mysql     -> mysql
mariadb   -> mariadb
oracle    -> oracle
postgres  -> postgres
```

Each adapter must use its own catalog queries and capability declaration.

### 19.8 Documentation and case-study acceptance

The repository must ship all of the following; a README section alone is not sufficient:

| Document | Required content |
|---|---|
| `README.md` | purpose, supported databases/harnesses, five-minute path, links to detailed guides |
| `docs/getting-started.md` | Python install, profile setup, SQLFluff bootstrap, first validated export |
| `docs/server-operations.md` | owner-started HTTP/MCP service, bind/auth modes, start/stop/health/doctor |
| `docs/command-reference.md` | every CLI command with syntax, inputs, outputs, exit codes, and examples |
| `docs/use-cases.md` | ask/all/selected flows, custom output path, ambiguity resolution, resume/retry |
| `docs/api-and-mcp-examples.md` | HTTP and MCP request/response examples tied to generated schemas |
| `docs/security.md` | credential boundary, read-only grants, masking, local/remote threat assumptions |
| `docs/troubleshooting.md` | connection, privilege, SQLFluff, parse, paging, hash, and cleanup failures |
| `docs/harnesses/codex.md` | install/discover Skill, connect MCP, invoke, verify tools |
| `docs/harnesses/claude-code.md` | install/discover Skill, connect MCP, invoke, verify tools |
| `docs/harnesses/gemini-cli.md` | install/discover Skill, connect MCP, invoke, verify tools |

Command and case documentation must use tables and provide **two or three examples for each applicable topic**. Minimum topics:

| Topic | Minimum example cases |
|---|---|
| installation/bootstrap | clean machine; already installed; offline/tooling unavailable |
| profiles | SQL Server env profile; PostgreSQL env profile; invalid raw-password profile |
| server startup | loopback HTTP+MCP; custom port; rejected remote bind without TLS/auth |
| SQLFluff | status/ensure; one bad file isolated; explicit version update and rollback |
| materialization | ask then select; explicit all; explicit selected categories |
| output path | default root; explicit nested relative path; rejected traversal |
| classification | ambiguous object proposed; owner override; leave unresolved |
| batching/resume | multi-page catalog; interrupted export resume; partial object failure |
| harness use | Codex; Claude Code; Gemini CLI |

Examples must show the command or prompt, preconditions, expected important output, and what the user should do next. Provide PowerShell and POSIX shell variants where syntax differs. Secrets must use placeholders and must never resemble production credentials.

### 19.9 Codex, Claude Code, and Gemini CLI compatibility

Use the open Agent Skills `SKILL.md` format as the canonical workflow. Package it for each harness without forking the instructions:

| Harness | Distribution metadata | Skill source | Default MCP connection | Required verification |
|---|---|---|---|---|
| Codex | `.codex-plugin/plugin.json` and `harnesses/codex/config.toml.example` | root `skills/sql-context-pack/SKILL.md`; repo authoring may link through `.agents/skills/` | owner-started loopback Streamable HTTP | Skill is discoverable; tools list; selected-category E2E passes |
| Claude Code | `.claude-plugin/plugin.json` and `.mcp.json.example` | root `skills/sql-context-pack/SKILL.md`; project authoring may link through `.claude/skills/` | owner-started loopback Streamable HTTP | plugin validates; Skill is discoverable; tools list; same E2E passes |
| Gemini CLI | root `gemini-extension.json` and `harnesses/gemini/settings.json.example` | root `skills/sql-context-pack/SKILL.md`; workspace authoring may link through `.gemini/skills/` or `.agents/skills/` | owner-started loopback Streamable HTTP | extension validates; Skill is discoverable; tools list; same E2E passes |

Compatibility rules:

- Do not pin the workflow to a proprietary model name. Test supported current models through their harnesses, but keep tool schemas and business behavior provider-neutral.
- Do not rely on vendor-only frontmatter in the canonical `SKILL.md`. Vendor-only metadata belongs in the vendor manifest or a thin wrapper that references the canonical Skill.
- Keep the canonical `SKILL.md` focused and below 500 lines; place detailed contracts/examples in one-level `references/` files and deterministic helpers in `scripts/`.
- Do not expose database credentials in plugin manifests, MCP tool arguments, prompts, or checked-in config.
- Connection examples may reference `SQLCTX_MCP_URL` and agent-scoped `SQLCTX_API_TOKEN`; actual values are owner-managed and ignored by version control. The owner-control credential has no harness environment-variable example.
- A harness package must not auto-enable remote access or weaken masking.
- A harness package must not claim plugin installation itself installed SQLFluff; it must run the shared status/ensure workflow before the first format.
- The same catalog fixture, expected calls, pagination behavior, clarification behavior, and validation result must be used for all three harness conformance tests.

### 19.10 Versioning and changelog acceptance

Keep these version domains separate:

| Version | Format | Meaning |
|---|---|---|
| specification version | `1.5` | current implementation-ready corrective specification; v1.4 remains an immutable archive |
| product/package/Skill version | SemVer, current `1.1.0` | server, CLI, canonical Skill, and three harness packages developed and released together; v1.4 established `1.0.0`, and this correction bumps patch without a prerelease suffix |
| `output_format_version` | monotonic schema version, for example `"1"` | canonical bundle/index compatibility field; bump only for incompatible format change |
| SQLFluff version | exact dependency version | package in the selected host Python; updated only by explicit tooling lifecycle |

Implementation and release rules:

1. Initialize every product surface at the current specification product version `1.1.0` in Chunk 0/1. Do not use `-dev.N`, `-rc`, or another prerelease suffix. `1.0.0` is the historical v1.4 baseline, not the version to initialize from this v1.5 specification.
2. Keep `pyproject.toml`, health output, canonical `SKILL.md`, Codex/Claude/Gemini manifests, generated schemas, and documentation on the same current version.
3. If testing or review discovers another defect that requires a repository change, increment patch once for that corrective iteration (`1.1.1`, `1.1.2`, and so on), add the fix to `CHANGELOG.md`, synchronize every version surface, and restart the gate. Aggregate related file edits into that one correction version.
4. Increment minor for a backward-compatible feature/scope expansion; increment major for a breaking public contract. Documentation-only corrections that do not change shipped artifacts may remain in the current version only before that version is released; after release they follow the next patch release.
5. Bump `output_format_version` only for an incompatible bundle/index schema change.
6. Phase A validates the current release version: formatting, lint, types, tests, security, documentation, generated contracts, packaging inputs, and a non-finalized changelog entry for that same version.
7. When Phase A passes, Phase B finalizes the date/artifacts/changelog for the same version, builds packages, verifies version consistency, and runs release smoke tests. Phase B never changes the version merely because the gate passed.
8. If Phase B exposes a code/spec/artifact defect requiring a change, bump patch, reopen Phase A, and do not release the failed version.

A version-consistency test must fail whenever any product surface differs.

Do not use `latest` as a released package/Skill version. Do not let each harness acquire an independent version in v1.

### 19.11 Cross-harness conformance acceptance

For Codex, Claude Code, and Gemini CLI, verify the same fixture scenario:

1. discover the canonical Skill,
2. connect to the owner-started MCP endpoint without exposing credentials,
3. list safe profiles and capabilities,
4. run ask mode and select `um` and `content`,
5. consume every preview and sitemap cursor,
6. analyze all objects despite selective materialization,
7. submit a non-authoritative semantic proposal with evidence references,
8. ask the owner once for unresolved categories,
9. export all batches and validate the assembled output,
10. produce equivalent normalized counts, files, indexes, and reports.

CI may use deterministic harness simulators for every commit. Before release, run opt-in smoke tests against the currently supported Codex, Claude Code, and Gemini CLI versions and record the tested harness versions. Exact natural-language wording may differ; schemas, safety invariants, call ordering, completeness, and artifacts must not.

The repository must create `.github/workflows/ci.yml` during the skeleton phase. It must run formatting, linting, type checking, unit, contract, integration, E2E, and harness-simulator jobs as those phases become available; a required phase may not remain absent merely because acceptance text mentions it.

### 19.12 Authoritative implementation context

- Commit this specification byte-for-byte as `docs/spec/design-spec-v1.5.md` and record its SHA-256 in `docs/spec/design-spec-v1.5.sha256`.
- Treat v1.5 as the only implementation source of truth; raw and v1.1–v1.4 remain archive/traceability inputs only.
- Every fresh implementation session reads `docs/implementation-state.md`, the immutable invariants, and only the routed v1.5 sections needed for its chunk.
- Do not rewrite the immutable contract from memory and do not load the full specification when the routed sections suffice.
- Token counts are tokenizer/model dependent; measure with the target harness tokenizer when needed and never present a hard-coded estimate as fact.
- Do not attribute authorship to a model unless verifiable metadata exists.

### 19.13 Final cut-off conformance

The release suite must prove all of the following:

- Restart the server mid-catalog and confirm resumed aliases and joins exactly match the pre-restart values using the same protected snapshot key.
- Start an export, attempt SQLFluff update, and confirm `409 TOOLING_BUSY`; after the export finishes, update and roll back through the same absolute host interpreter, confirm the executable fingerprint never changes, and confirm no Python environment is created anywhere by sqlctx.
- Traverse multi-page category preview over HTTP and MCP tools and terminate only at `next_cursor=null`; no paginated catalog resource is advertised.
- Create retained jobs with the same profile but different schemas, filters, sample/masking/selection policies, or object batches; rediscovery must resume only the exact fingerprint match.
- Reject public export input attempting `sqlfluff:false` or `append_samples:false`.
- Compare completed HTTP/MCP `ExportStatus` integrity fields and make fetch fail on declared size, bundle hash, or manifest hash mismatch.
- Corrupt, omit, add, duplicate, or move a managed destination file after extraction; local assembled-inventory validation must fail each case.
- Attempt every privileged operation with an agent token only, an unrelated/modified/expired/replayed approval, and a valid request-bound owner approval; only the final case succeeds once.
- Expire a catalog while a dependent export remains active/unexpired; cleanup must retain all required catalog/masking state, then remove it only after the dependency releases.
- Exercise Phase A failure, corrective patch bump, Phase A success, Phase B success, and Phase B defect paths; no path requires a changelog/version state that can exist only after the same gate passes.
- Verify the current implementation product version is exactly `1.1.0` without a prerelease suffix on every product surface.

---

# 20. Chunked Implementation Prompts

Use the following prompts sequentially in the same repository, but run **one chunk per fresh agent session**. Do not skip a chunk. Every session must first read `docs/implementation-state.md`, `CHANGELOG.md`, the immutable invariants, the routed sections named by its chunk, and only the relevant code. It must update implementation state before stopping. v1.5 is the sole source of truth; do not use raw or v1.1–v1.4 as implementation context.

Section-routing index:

| Chunk | Required v1.5 sections |
|---|---|
| 0 | 1–4, 19.10–19.13, 20 |
| 1 | 1.3, 3, 6, 11.20, 12, 19.1, 19.10–19.13 |
| 2 | 4–5, 8–9, 17.5–17.6 |
| 3 | 6–8, 10, 17 |
| 4 | 10, 13–16, 19.3–19.6 |
| 5 | 4.5, 11–12, 17.3–17.6 |
| 6 | 13, 15, 17–18, 21 |
| 7 | 1.3, 3.4–3.5, 19, 21–23 |

---

## Prompt Chunk 0 — Immutable Contract

```text
You are implementing a repository named `sql-context-pack`.

This is a fresh Chunk 0 session. Read `docs/spec/design-spec-v1.5.md` Sections 1–4, 19.10–19.13, and 20 before changing implementation files. If the file is not present yet, copy the supplied v1.5 artifact byte-for-byte into that path, record its SHA-256, and then read those routed sections. Do not summarize or regenerate the authoritative specification.

IMMUTABLE PRODUCT PURPOSE
Build a universal, Python-first system that extracts database table DDL and stored procedure definitions, retrieves sanitized representative table samples, formats SQL using SQLFluff, classifies objects into business categories, creates dependency/relationship/tag indexes, and packages the result as AI-ready SQL context.

SUPPORTED DATABASE ENGINES AND SQLFLUFF DIALECTS
- sqlserver -> tsql
- mysql -> mysql
- mariadb -> mariadb
- oracle -> oracle
- postgres -> postgres

MANDATORY ARCHITECTURE
- Python is the primary implementation language.
- Use one GitHub monorepo named `sql-context-pack`; do not split server, CLI, Skill, or harness packages into separate repositories in v1.
- One shared application core must serve both HTTP and MCP interfaces.
- The owner configures credentials and starts the server before an agent connects.
- Default MCP transport is owner-started Streamable HTTP on loopback; client-managed STDIO is explicit opt-in only.
- Agents/models must never receive database credentials or connection strings.
- Harnesses receive only an agent-scoped token; owner-control credentials never enter harness configuration, and privileged operations require a request-bound one-time owner approval.
- Use connection profile names in all public interfaces.
- No arbitrary SQL endpoint or MCP tool is allowed.
- Database access is read-only.
- Cleansing/masking must happen before any value is serialized, logged, returned, or written.
- SQLFluff formatting must run one SQL file at a time and only for final-materialization SQL after Pass 2 and owner resolution.
- Production uses the owner-selected Python already installed on the user's machine: an owner-configured absolute executable or the running process's `sys.executable`.
- Every SQLFluff parse/format/version/install/update command must use that exact host interpreter explicitly.
- The Skill, plugin, server, CLI, bootstrap helpers, and tests must never create, copy, activate, or manage a virtualenv/venv, conda environment, pipx environment, or bundled Python.
- If Python >=3.11 is absent, non-Python preflight helpers provide official platform installation guidance and stop with `PYTHON_UNAVAILABLE`; they do not install Python or modify PATH.
- A parse failure in one file must not stop other files.
- Never enable fix_even_unparsable or --FIX-EVEN-UNPARSABLE.
- Temporary files must use the operating-system temp/runtime directory.
- Do not create `.tmp-*` or staging folders inside the target project.
- Classification must use two passes: preliminary name-based discovery, then final relationship-aware classification.
- If the user has not selected all or named categories, the skill must ask whether to materialize all categories or selected categories.
- User category selection controls final materialization only.
- Every permitted object must still be fully extracted and analyzed regardless of selected categories.
- Final materialization must use Pass 2 categories, never Pass 1 object membership.
- Unknown business categories must not be guessed; they must be returned as consolidated owner decisions.
- The server must not call provider model APIs; the active harness/model may submit only sanitized, non-authoritative classification proposals.
- Selective output must preserve excluded connected objects as boundary metadata.
- Output business category folders must be directly under the selected output root.
- The skill must use cursor pagination until next_cursor is null.
- The skill must separately validate discovered, analyzed, failed-analysis, materialized, and intentionally-excluded counts before claiming completion.
- SQLFluff must be a pinned dependency and must also have an executable ensure/status/update lifecycle.
- Normal export must never silently update SQLFluff.
- Version 1 supports tables and stored procedures as required object types.
- Graph-ready metadata is required; graph rendering itself is a later phase.
- Maintain one canonical `skills/sql-context-pack/SKILL.md` and package it for Codex, Claude Code, and Gemini CLI without copied workflow logic.
- Start every product surface at the current `1.1.0` with no prerelease suffix. `1.0.0` is the archived v1.4 baseline; a later corrective iteration bumps patch and restarts the gate under Section 19.10.
- Ship case-by-case operator, command, API/MCP, troubleshooting, security, and per-harness guides with two or three examples per applicable topic.
- Version 1 uses deterministic sampling only and fixed index-only boundary metadata; no relationship-aware sampling or direct/closure dependency materialization.
- Catalog/export jobs have fingerprinted paginated rediscovery, cooperative cancel, request-bound owner-approved delete, dependency-aware 24-hour completed retention defaults, and a configurable 5 GiB runtime quota.
- HTTP create calls require `Idempotency-Key`; MCP create tools require `idempotency_key`; both map to the same application contract.
- Bundle transfer uses only `sqlctx export fetch` over authenticated loopback HTTP; never ZIP/base64 through MCP.

STRICT SCOPE
Do not add:
- arbitrary database querying,
- data mutation,
- schema migration,
- text-to-SQL,
- vector databases,
- web admin UI,
- cloud credential vault,
- creation or management of Python virtual environments,
- continuous scheduling,
- database backup/restore,
- unrelated framework abstractions.

IMPLEMENTATION QUALITY
- Use typed Python.
- Validate inputs.
- Sanitize errors.
- Add unit and integration tests.
- Keep interface handlers thin.
- Put database-specific logic only in adapters.
- Use atomic writes.
- Be cross-platform for Windows, Linux, and macOS.

Before implementation, create:
1. `docs/spec/design-spec-v1.5.md` as the byte-for-byte authoritative artifact plus `docs/spec/design-spec-v1.5.sha256`,
2. `docs/implementation-state.md`,
3. `docs/requirements.md`,
4. `docs/architecture.md`,
5. `docs/security.md`,
6. `docs/output-format.md`,
7. `docs/acceptance-criteria.md`,
8. `docs/versioning.md`,
9. `docs/harness-compatibility.md`,
10. `CHANGELOG.md`,
11. a central version source initialized at `1.1.0` with no prerelease suffix.

Derived documents must link to exact authoritative sections and add implementation decisions only where necessary. They must not become rewritten substitute contracts. Before stopping, record completed Chunk 0 in `CHANGELOG.md` and `docs/implementation-state.md`; keep `1.1.0` unless Section 19.10 requires a later corrective patch bump.

After creating the documents, stop and report:
- files created,
- architecture boundaries,
- unresolved implementation risks only.
Do not implement runtime code in this chunk.
```

---

## Prompt Chunk 1 — Repository Skeleton and Core Domain

```text
Continue the existing `sql-context-pack` repository.

This is a fresh Chunk 1 session. Read `docs/implementation-state.md`, `CHANGELOG.md`, and `docs/spec/design-spec-v1.5.md` Sections 1.3, 3, 6, 11.20, 12, 19.1, and 19.10–19.13. Do not reinterpret or weaken the immutable contract.

Implement only the repository skeleton and shared core/domain contracts.

REQUIRED MODULES
- src/sqlctx/core
- src/sqlctx/application
- src/sqlctx/adapters
- src/sqlctx/security
- src/sqlctx/formatting
- src/sqlctx/classification
- src/sqlctx/indexing
- src/sqlctx/exporting
- src/sqlctx/server/http
- src/sqlctx/server/mcp
- src/sqlctx/cli

REQUIRED SHARED SKILL/PACKAGING SKELETON
- skills/sql-context-pack/SKILL.md
- .codex-plugin/plugin.json
- .claude-plugin/plugin.json
- gemini-extension.json
- harnesses/codex
- harnesses/claude
- harnesses/gemini
- scripts/python-preflight.ps1
- scripts/python-preflight.sh

Create only minimal valid manifests in this chunk. Do not duplicate SKILL.md into vendor directories. The preflight scripts detect Python >=3.11 and print platform-specific installation guidance only; they must not install Python, elevate privileges, modify PATH, or create an environment.

REQUIRED DOMAIN MODELS
- ConnectionProfileDescriptor
- ResolvedConnectionProfile (internal only; never serializable publicly)
- DatabaseCapabilities
- DatabaseObject
- ObjectRef
- ColumnMetadata
- ConstraintMetadata
- ForeignKeyMetadata
- DependencyEdge
- CatalogSnapshot
- CatalogStatus
- SitemapPage
- CategoryPreview
- CategoryPreviewGroup
- MaterializationSelection
- MaterializationPlan
- ClassificationPassResult
- ClassificationCandidate
- ClassificationProposal
- ClassificationProposalBatch
- ProposalBatchResult
- ClassificationRequest
- ClassificationResolution
- SampleRequest
- SamplePage
- MaskingDecision
- SqlFormatResult
- ExportBatchRequest
- ExportJob
- ExportArtifact
- CatalogJobPage
- ExportJobPage
- DeleteResult
- AssembledInventory
- ApprovalChallenge
- HostPythonToolingDescriptor
- ValidationResult

REQUIRED PORTS/INTERFACES
- ConnectionProfileRepository
- DatabaseAdapter
- MaskingEngine
- SqlFormatter
- CategoryClassifier
- DependencyAnalyzer
- ExportStore
- RuntimeStateStore
- AuditSink

RULES
- Public models must not contain raw credentials or a connection string.
- Internal resolved profiles must have redacted repr/string behavior.
- Use explicit enums for engine, object type, job status, edge type, sensitivity class, classification status, classification pass, materialization mode, and inclusion reason. Dependency handling is fixed index-only behavior, not a public mode enum.
- Add JSON-schema-compatible validation models for HTTP/MCP boundaries.
- Do not implement database queries yet.
- Do not implement HTTP routes or MCP tools yet.
- Add tests proving sensitive fields cannot be serialized from internal profile objects.

Add `pyproject.toml` with `requires-python = ">=3.11"` and a pinned/locked dependency strategy. Include SQLFluff as a required dependency but keep the actual version in one central dependency definition.

Create `.github/workflows/ci.yml` now. It must run formatting, linting, type checking, and current unit/contract tests; add integration, E2E, and harness-simulator jobs as their code appears rather than leaving CI creation until release.

Add one central product version and tests proving the same value is exposed by Python package metadata and all three harness manifests. Initialize `CHANGELOG.md` with the current version.

Before stopping, update `CHANGELOG.md` and `docs/implementation-state.md`; keep the current product version unless a failed review/gate required the corrective bump defined in Section 19.10.

Stop after:
- core modules compile,
- type checks pass,
- unit tests pass.

Report only:
- module tree,
- key contracts,
- test results,
- remaining risks.
```

---

## Prompt Chunk 2 — Profiles, Security, Masking, and SQLFluff Manager

```text
Continue the existing `sql-context-pack` repository.

This is a fresh Chunk 2 session. Read `docs/implementation-state.md`, `CHANGELOG.md`, and `docs/spec/design-spec-v1.5.md` Sections 4–5, 8–9, and 17.5–17.6. Do not modify the immutable product scope.

Implement:
1. connection profile loading,
2. secret resolution from environment,
3. masking/classification engine,
4. routine/DDL secret scanning,
5. SQLFluff lifecycle manager,
6. fail-isolated per-file formatter.

CONNECTION PROFILE REQUIREMENTS
- Load profile metadata from the platform config directory.
- Profile files must reference environment variable names.
- Reject raw password fields by default.
- Public profile listing returns only profile name, engine, allowed schemas, allowed object types, and readiness.
- Never log resolved secrets.
- Internal resolved profile repr must redact all secret values.
- Validate allowed schemas and sample limits.
- Implement separate agent and owner credentials with owner-only metadata: mode `0600` on POSIX, current-user/`SYSTEM` ACL on Windows; neither credential may appear on stdout or a command line.

MASKING REQUIREMENTS
Implement at minimum:
- national_id
- username
- password
- password_hash
- secret
- secret_key
- private_key
- api_key
- client_secret
- access_token
- refresh_token
- jwt
- session_token
- cookie
- email
- phone
- address
- personal_name
- financial_account
- credit_card
- date_of_birth
- precise_location
- biometric

Apply:
1. owner override,
2. database classification metadata,
3. exact column-name rule,
4. tokenized column-name rule,
5. type/length heuristic,
6. value-pattern detector,
7. routine/DDL literal scanner,
8. fail-closed fallback.

High-risk secrets must be fully redacted.
Keys that must preserve relationships must use deterministic HMAC-based aliases within one snapshot.
Encode a Crockford Base32 HMAC digest prefix into aliases such as `user_k7m2q9x4p1`; do not use query-order sequence numbers.
Persist the collision-checked per-snapshot alias registry in the protected runtime store so retry/resume is stable; extend the digest prefix deterministically on collision.
Persist or deterministically derive the same protected snapshot masking key for cross-process resume, retain it through dependent-export lifetime, and fail explicitly when secure resume is unavailable.
Never store the HMAC key in an export.

SQLFLUFF LIFECYCLE
Required CLI/application commands:
- doctor
- sqlfluff status
- sqlfluff ensure
- sqlfluff update
- sqlfluff update --version X.Y.Z

Ensure behavior:
- resolve the owner-configured absolute host interpreter or `sys.executable`, require Python >=3.11, and never later fall back to ambient PATH,
- check importlib metadata and run `<host-python> -m sqlfluff version` through that exact executable,
- reuse a valid install,
- use a cross-process lock,
- after owner approval, install the pinned version once with `<host-python> -m pip install --user "sqlfluff==<PINNED_VERSION>"` when missing from a supported base interpreter,
- verify after install,
- store only protected host-interpreter/tooling state outside the project; never store or create an interpreter/environment there,
- treat an explicitly selected pre-existing owner-managed virtual/conda environment as verify/execute-only and return manual guidance when its SQLFluff is missing or mismatched,
- never create, copy, activate, update, repair, or delete a virtualenv/venv, conda environment, pipx environment, or bundled Python,
- never use `sudo pip`, `--break-system-packages`, pipx, or project-local package installation,
- never install latest during a normal export.

Update behavior:
- explicit invocation only,
- reject with `409 TOOLING_BUSY` while any export or formatting job is active,
- update the exact package in the same selected host interpreter's user site,
- self-test through that same interpreter and atomically update tooling state,
- reinstall and verify the previous exact version on failure,
- never change interpreter or create a fallback environment.

FORMATTER
Run the formatter only for final-materialization SQL after Pass 2 and owner resolution. Do not format the full analysis snapshot. For each materialized file independently:
1. keep cleaned original SQL,
2. create OS temp file,
3. parse with `<host-python> -m sqlfluff parse` using the export-pinned interpreter/tooling fingerprint,
4. if parse fails, preserve cleaned original and continue,
5. if parse succeeds, format with:
   `<host-python> -m sqlfluff format --exclude-rules "CP02,LT01,RF06" --dialect <dialect> --templater raw <file>`
6. parse formatted result again,
7. rollback to cleaned original if post-format parse fails,
8. return structured SqlFormatResult,
9. delete temp files in finally.

Never format a directory from the production orchestrator.
Never enable fix_even_unparsable.

TESTS
- clean/missing SQLFluff simulation,
- repeated ensure does not reinstall,
- concurrent ensure installs once,
- failed update rolls back,
- one bad SQL file does not stop good files,
- no project-local temp directory,
- raw secrets absent from output/logs/errors,
- stable alias preserves joins.
- cross-process resume reuses the same protected snapshot key and aliases; unavailable secure persistence fails explicitly.
- Python-missing/unsupported preflight returns `PYTHON_UNAVAILABLE` with Windows/macOS/Linux owner installation guidance and performs no installation or environment mutation.
- every SQLFluff subprocess uses the selected absolute host interpreter; a different ambient PATH interpreter is ignored.
- update during active export/format work returns `409 TOOLING_BUSY`; idle update and rollback use the same interpreter fingerprint.
- no virtualenv/venv/conda/pipx/bundled-Python directory or package payload is created beneath `skills/`, the target project, or the sqlctx runtime store.
- a pre-existing owner-managed environment is never mutated automatically.
- owner credential unlock/grant rejects non-interactive invocation and never reaches harness configuration.

Before stopping, update `CHANGELOG.md` and `docs/implementation-state.md`; keep the current product version unless Section 19.10 requires a corrective patch bump. Stop after tests pass.
```

---

## Prompt Chunk 3 — Database Adapters and Catalog Discovery

```text
Continue the existing `sql-context-pack` repository.

This work is deliberately split into fresh sessions. Each sub-chunk reads `docs/implementation-state.md`, `CHANGELOG.md`, and `docs/spec/design-spec-v1.5.md` Sections 6–8, 10, and 17:

- Chunk 3A: shared adapter contract/fixtures plus PostgreSQL and MySQL.
- Chunk 3B: MariaDB as a distinct adapter plus SQL Server.
- Chunk 3C: Oracle, catalog orchestration, paging, cancellation, retention/quota integration, and cross-adapter contract tests.

Complete, test, document, and update implementation state per sub-chunk before starting the next fresh session. Keep the current product version unless Section 19.10 requires a corrective patch bump.

Implement database adapters for:
- SQL Server
- MySQL
- MariaDB
- Oracle
- PostgreSQL

Do not expose arbitrary SQL.

Every adapter must implement:
- test_connection
- get_server_info
- list_schemas
- discover_objects
- get_table_definition
- get_procedure_definition
- get_table_columns
- get_constraints
- get_foreign_keys
- get_sample_rows
- get_routine_dependencies
- capability declaration

DIALECT MAPPING
- sqlserver -> tsql
- mysql -> mysql
- mariadb -> mariadb
- oracle -> oracle
- postgres -> postgres

SECURITY
- Use reviewed adapter-owned query templates.
- Validate identifiers against discovered catalog objects.
- Quote identifiers through adapter functions.
- Enforce allowed schemas and object types.
- Apply statement timeouts.
- Use bounded connection pools.
- Use read-only transaction behavior where supported.
- Do not put raw SQL values in logs or errors.
- Propagate cooperative job cancellation to an active database statement when the driver supports it; otherwise stop scheduling and close/rollback safely at the next boundary.

SAMPLING
- Minimum and default requested target is 10 rows per eligible table.
- Reject a configured/requested target below 10.
- If at least the requested number of eligible rows exists, return exactly the requested number.
- If fewer exist or policy removes rows, return actual count and shortage metadata.
- Never fabricate duplicate rows.
- Support deterministic sampling only; do not implement `relationship_aware` sampling.
- Prefer primary-key deterministic ordering.
- Fall back to unique index, then safe adapter strategy.
- Mark non-deterministic samples.
- Do not perform expensive random full-table sorts.
- Exclude/placeholder large binary values.
- Limit long text.

CATALOG PHASES
Implement two catalog phases:

Phase 1:
- discover every permitted object name,
- return preliminary category preview,
- support `awaiting_selection`,
- do not treat preliminary categories as final.

Phase 2:
- after selection is recorded, extract every permitted object,
- selection must not modify database include/exclude filters,
- build complete relationships and dependencies,
- support final contextual classification.

CATALOG
Implement a stable CatalogSnapshot containing:
- objects,
- columns,
- constraints,
- foreign keys,
- routine dependencies,
- native comments/descriptions,
- content/source fingerprints,
- capability metadata.

Use cursor pagination for the sitemap.
Response must include recommended and maximum batch sizes.
Implement paginated safe catalog-job rediscovery plus the 24-hour completed-catalog retention and shared 5 GiB runtime quota. Never delete active or unexpired work silently.

MARIADB
Keep MariaDB as a distinct adapter even where implementation shares reusable MySQL helpers.

ORACLE
Discover privileges/capabilities at runtime. Do not assume DBMS_METADATA or all catalog views are always available.

TESTS
- adapter contract tests,
- identifier safety,
- allowed schema enforcement,
- sample limit,
- below-10 request rejection,
- fewer-than-10 behavior,
- deterministic ordering,
- capability negotiation,
- sanitized database errors,
- catalog expiry remains pinned by active/unexpired dependent exports and releases only after dependency cleanup.

Use containerized integration tests where practical and fixture/mocked adapter tests where a proprietary database cannot run in CI.

Stop after all three sub-chunks' adapter and catalog tests pass and `docs/implementation-state.md` records the next routed chunk.
```

---

## Prompt Chunk 4 — Classification, Indexes, Output Packaging, and Validation

```text
Continue the existing `sql-context-pack` repository.

This is a fresh Chunk 4 session. Read `docs/implementation-state.md`, `CHANGELOG.md`, and `docs/spec/design-spec-v1.5.md` Sections 10, 13–16, and 19.3–19.6.

Implement:
1. category rule configuration,
2. Pass 1 preliminary name-based classification,
3. user materialization selection,
4. Pass 2 relationship-aware final classification,
5. classification-change tracking,
6. sanitized non-authoritative model proposal intake,
7. unresolved owner-decision workflow,
8. materialization planning,
9. relationship/dependency indexes,
10. graph-ready indexes,
11. output package writer,
12. integrity validation.

TWO-PASS RULE
Pass 1 may use only names, schema, type, configured rules, and lightweight comments.
It exists to show a category preview.

After the user selects all or selected categories, the system must still analyze every permitted object.

Pass 2 must use:
1. owner override
2. exact configured match
3. configured prefix
4. schema ownership
5. database comment/extended property
6. columns, types, and key roles
7. foreign-key neighborhood
8. routine read/write/call dependencies
9. sanitized sample shape/representative values
10. name-token similarity
11. validated sanitized semantic proposal from the active harness/model, if any

The server must not import a Codex, Anthropic, or Gemini model SDK and must not call a provider model API.

Materialization must apply selected category names to Pass 2 categories.
Track objects moved into and out of selected categories.

Only owner override or deterministic configured rules supported by context may produce final `confirmed`.
Ambiguous objects must be `unresolved`.
Do not guess.

Model proposals:
- accept only known object/category/evidence IDs,
- record harness and Skill-version provenance,
- may produce suggested/unresolved only,
- never impersonate an owner resolution,
- remain optional so deterministic export still works without a model proposal.

Classification request must include:
- all current categories,
- suggested new categories,
- all unresolved objects,
- candidates,
- confidence,
- evidence,
- unresolved option.

Every classification-request response is cursor-paginated with `items` and `page: {limit, returned, next_cursor}`.

Persist owner decisions in `config/category-overrides.yaml`.
Enforce request-bound owner approval before accepting an authoritative resolution or persistent override; agent-scoped model proposals remain non-authoritative.
Allow reclassification without a new database extraction.

RELATIONSHIPS
Emit nodes and edges for:
- database
- schema
- category
- table
- procedure
- column

Edge types:
- CONTAINS
- BELONGS_TO_CATEGORY
- HAS_COLUMN
- PRIMARY_KEY
- FOREIGN_KEY
- READS_FROM
- WRITES_TO
- CALLS
- REFERENCES
- DERIVED_FROM

Cardinality:
- prove 1:1 using child uniqueness,
- use 1:N otherwise for a confirmed FK,
- infer M:N only from a valid associative-table structure,
- mark unproven relationships as inferred/unknown.

OUTPUT LAYOUT
Business categories must be directly under output root:

<output-root>/
  manifest.yaml
  catalog.json
  categories.yaml
  <category>/
    category.yaml
    tables/
    store_procedures/
  unresolved/
  indexes/
  reports/

TABLE SQL
- After Pass 2 and owner resolution, format cleaned DDL only for final-materialization files.
- Append sanitized sample JSON rows as SQL line comments.
- Include requested and actual sample counts.
- Do not run SQLFluff over the sample comment block.

INDEXES
- objects.jsonl
- nodes.jsonl
- edges.jsonl
- relationships.json
- routine-dependencies.json
- tags.json
- graph.json

REPORTS
- export-summary.md
- category-preview.json
- classification-report.json
- materialization-plan.json
- masking-report.json
- sqlfluff-report.json
- integrity-report.json

PATH SAFETY
- Reject absolute/traversal paths inside bundles.
- Do not overwrite unmanaged files.
- Use a managed-file manifest.
- Write atomically.
- Use OS temp extraction.
- Never create project-local `.tmp-*`.

VALIDATION
Check:
- discovered/analyzed/failed-analysis accounting,
- materialized/intentionally-excluded accounting,
- Pass 1 to Pass 2 category changes,
- selected-category materialization decisions,
- boundary relationships for excluded connected objects,
- duplicate paths,
- hashes,
- path traversal,
- unresolved relationship targets,
- raw-secret scan,
- SQLFluff result coverage,
- sample metadata,
- canonical manifest `output_format_version` and validation `expected_output_format_version`.
- local re-read of every assembled managed file and comparison of relative-path/size/SHA-256 inventory against immutable export manifests.

The SQLFluff coverage invariant is `format_requested = formatted + parse_failed_preserved + format_failed_preserved = materialized_object_count`. Analysis-failed and intentionally excluded objects are outside formatting scope. Selective dependency behavior is fixed index-only boundary metadata; do not implement direct or closure modes.

Stop after unit tests pass and provide one realistic fixture output containing:
- um/tables/UM_USER.sql
- um/tables/UM_ROLE.sql
- content/tables/CONTENT.sql
- content/tables/CONTENT_SHARE.sql
- at least one stored procedure per category
- one unresolved object
- relationship and graph indexes.

Before stopping, update `CHANGELOG.md` and `docs/implementation-state.md`; keep the current product version unless Section 19.10 requires a corrective patch bump.
```

---

## Prompt Chunk 5 — HTTP and MCP Interfaces

```text
Continue the existing `sql-context-pack` repository.

This is a fresh Chunk 5 session. Read `docs/implementation-state.md`, `CHANGELOG.md`, and `docs/spec/design-spec-v1.5.md` Sections 4.5, 11–12, and 17.3–17.6. The endpoint/tool lists below are only an index; implement exact request, response, pagination, idempotency, authorization, integrity, and example contracts from Sections 11–12. Do not invent schemas from this abbreviated list.

Implement thin HTTP and MCP interfaces over the existing application core.

HTTP BASE
/api/v1

HTTP ENDPOINTS
- GET  /health
- GET  /capabilities
- GET  /profiles
- POST /profiles/{profile}/test
- GET  /catalogs
- POST /catalogs
- GET  /catalogs/{catalog_id}
- POST /catalogs/{catalog_id}/cancel
- DELETE /catalogs/{catalog_id}
- GET  /catalogs/{catalog_id}/category-preview
- POST /catalogs/{catalog_id}/selection
- GET  /catalogs/{catalog_id}/sitemap
- GET  /catalogs/{catalog_id}/materialization-plan
- GET  /catalogs/{catalog_id}/classification-requests
- POST /catalogs/{catalog_id}/classification-proposals
- POST /catalogs/{catalog_id}/classification-resolutions
- GET  /exports
- POST /exports
- GET  /exports/{export_id}
- POST /exports/{export_id}/cancel
- DELETE /exports/{export_id}
- GET  /exports/{export_id}/bundle
- GET  /exports/{export_id}/manifest
- GET  /exports/{export_id}/report
- POST /validations
- GET  /tooling/sqlfluff
- POST /tooling/sqlfluff/ensure
- POST /tooling/sqlfluff/update

Use:
- 200 synchronous success
- 202 accepted job
- 400 invalid input
- 401 unauthenticated
- 403 policy denial or approval required
- 404 not found
- 409 state conflict
- 413 too large
- 422 unsupported capability
- 429 throttled
- 500 sanitized internal error
- 503 dependency unavailable
- 507 runtime storage full

Default HTTP bind:
- 127.0.0.1
- separate random agent bearer and owner control credential
- remote mode disabled
- write separate owner-only agent connection metadata and owner-control metadata in the user runtime directory
- stdout may print only the MCP URL and agent metadata path, never either credential

Default MCP transport:
- owner-started Streamable HTTP at `/mcp` on the same loopback service
- agent bearer comes from harness configuration, never a tool argument; privileged operations require a server-side owner grant
- STDIO is disabled unless the owner explicitly opts into client-managed lifecycle

MCP TOOLS
- sqlctx_get_capabilities
- sqlctx_list_profiles
- sqlctx_test_profile
- sqlctx_list_catalogs
- sqlctx_create_catalog
- sqlctx_get_catalog_status
- sqlctx_cancel_catalog
- sqlctx_delete_catalog
- sqlctx_get_category_preview
- sqlctx_set_materialization_selection
- sqlctx_list_sitemap
- sqlctx_get_materialization_plan
- sqlctx_get_classification_requests
- sqlctx_submit_classification_proposals
- sqlctx_resolve_classifications
- sqlctx_list_exports
- sqlctx_export_batch
- sqlctx_get_export_status
- sqlctx_cancel_export
- sqlctx_delete_export
- sqlctx_validate_exports
- sqlctx_sqlfluff_status
- sqlctx_sqlfluff_ensure
- sqlctx_sqlfluff_update

MCP RESOURCES
- sqlctx://export/{export_id}/manifest
- sqlctx://export/{export_id}/report

MCP RULES
- strict JSON schemas,
- structured output,
- cursor pagination,
- no credentials,
- no arbitrary SQL,
- no unrestricted filesystem path,
- sanitized errors,
- meaningful text plus structured content.

IDEMPOTENCY AND BUNDLE TRANSFER
- HTTP catalog/export creation requires `Idempotency-Key`; MCP creation requires `idempotency_key`; normalize both into the same application property.
- Same scoped key plus same normalized request returns the original job; a changed request returns `IDEMPOTENCY_CONFLICT`.
- Implement `sqlctx export fetch --export-id ...` as the only Skill-facing bundle path. It consumes HTTP binary internally from protected connection metadata and validates size/hash/manifest/path safety in OS temp.
- Never return ZIP/base64 or an unrestricted local runtime path through MCP.
- Remove public `sqlfluff`/`append_samples` disable switches; reject explicit `false` as `MANDATORY_EXPORT_STAGE_DISABLED`.
- Completed `ExportStatus` returns immutable `size_bytes`, bundle `sha256`, `manifest_sha256`, host-Python executable fingerprint/version, SQLFluff version, tooling fingerprint, and `output_format_version` over both interfaces; it never exposes the protected absolute interpreter path.
- Rediscovery descriptors include exact normalized request/batch fingerprints; never resume from profile/status alone.
- Privileged delete, authoritative resolution, and any SQLFluff install/update package mutation require a request-bound one-time owner approval; an agent token alone receives `APPROVAL_REQUIRED`.
- Validation consumes the local validator's complete assembled-file inventory, not server job IDs alone.

Generate HTTP OpenAPI and MCP input/output schemas from shared typed models. Add a contract table and request/response/error examples to `docs/api-and-mcp-examples.md`. Contract tests must compare normalized HTTP and MCP results for the same scenarios.

Use the current stable official MCP Python SDK and pin an exact compatible version.
Do not use a pre-release SDK in production unless the repository explicitly opts into it and tests it.

Add API contract tests and MCP Inspector-compatible tests covering exact rediscovery fingerprints, standard preview envelopes, mandatory-stage rejection, HTTP/MCP export integrity parity, assembled inventory mismatch, absence of catalog resources, and owner-approval denial/binding/expiry/single-use behavior.
Before stopping, update `CHANGELOG.md` and `docs/implementation-state.md`; keep the current product version unless Section 19.10 requires a corrective patch bump. Stop after all interface tests pass.
```

---

## Prompt Chunk 6 — Agent Skill and End-to-End Acceptance

```text
Continue the existing `sql-context-pack` repository.

This is a fresh Chunk 6 session. Read `docs/implementation-state.md`, `CHANGELOG.md`, and `docs/spec/design-spec-v1.5.md` Sections 13, 15, 17–18, and 21.

Implement the `sql-context-pack` Agent Skill.

The skill must execute this exact workflow:

1. Parse the user request.
2. Resolve output directory: explicit path, configured default, or repository-root/sql-context.
3. Resolve initial materialization mode: ask, all, or selected.
4. Discover server capabilities.
5. List safe profiles and page through recent catalog/export jobs; resume only an exact normalized request/selection/batch fingerprint match.
6. Select only an explicit or unambiguous profile.
7. Ensure SQLFluff is available.
8. Test profile connectivity.
9. Resume the matching catalog when safe; otherwise create it with a new idempotency key and the two-pass category policy.
10. Poll until preliminary classification is available.
11. Fetch all category-preview pages using next_cursor.
12. If mode=ask:
    a. present all preliminary categories and counts;
    b. present representative names;
    c. present unresolved count;
    d. ask whether to create all categories or selected categories;
    e. store the selected category names.
13. Submit materialization selection.
14. Confirm from the response that analysis_scope.restricted_by_selection=false.
15. Poll while the server extracts every permitted object.
16. Poll through relationship analysis and final classification.
17. Fetch every analysis sitemap page until next_cursor is null.
18. Record all discovered, analyzed, failed-analysis, and unresolved object IDs.
19. Fetch the final materialization plan.
20. Inspect moved-into/moved-out categories, connected new categories, and boundary relationships.
21. Fetch every classification-request page until next_cursor is null.
22. Optionally submit sanitized model proposals with harness/Skill provenance; never submit them as owner decisions.
23. Refresh every classification-request page after proposal validation.
24. If owner decisions are required, ask one consolidated question, obtain server-enforced request-bound owner approval, submit resolutions, and refresh final classification and the materialization plan.
25. Fetch every materialization sitemap page until next_cursor is null.
26. Collect only final included object IDs for export.
27. Record every intentionally excluded object and reason.
28. Partition included object IDs by recommended batch size and weight.
29. Export every materialization batch with a stable per-batch idempotency key.
30. Poll every export.
31. Fetch every completed bundle through `sqlctx export fetch`; never transfer ZIP/base64 through MCP.
32. Validate declared size, bundle/manifest hashes, and path safety in OS temp.
33. Assemble only managed files into the selected output root.
34. Run `sqlctx validate output` to reopen/hash every assembled managed file, then call final validation with the complete assembled inventory, expected counts, `expected_output_format_version`, and all export IDs.
35. Verify:
    discovered = analyzed + failed_analysis
    analyzed = materialized + intentionally_excluded
36. Write reports and manifest.
37. Remove all OS temporary files in finally.
38. Report exact analysis, materialization, exclusion, warning, unresolved, and failure counts.

At every polling or extraction step, honor owner/client cancellation through the catalog/export cancel operations. Use fingerprinted paginated list operations for rediscovery after interruption and request-bound owner-approved delete only for deliberate cleanup.

The skill must never:
- ask for a database password,
- print credentials or environment variables,
- call arbitrary SQL,
- stop after the first category-preview or sitemap page,
- use selected categories to restrict database extraction,
- finalize categories from Pass 1 names alone,
- apply selection to preliminary object IDs,
- guess categories,
- send raw SQL or unmasked sample values to a model provider,
- mark a model proposal as an owner resolution,
- fabricate rows,
- request fewer than 10 sample rows per eligible table,
- weaken masking,
- format a whole directory,
- enable fix-even-unparsable,
- silently update SQLFluff,
- write `.tmp-*` in the project,
- overwrite unmanaged files,
- hide failures,
- claim completion before validation,
- read or print the bearer token,
- pass a bearer token as a tool or command-line argument,
- download a bundle through an MCP resource,
- implement direct/closure dependency materialization or relationship-aware sampling.

OUTPUT PATH LANGUAGE
Recognize user intent such as:
- “เขียนไปที่ …”  ("write to ...")
- “output …”
- “สร้างที่ …”
- “write to …”
- “generate under …”

TEST SCENARIOS
1. Clean machine without SQLFluff.
2. 842 objects across multiple sitemap pages.
3. One database connection transient failure.
4. Three unparsable procedures while other files succeed.
5. Sensitive values in rows and stored procedure source.
6. Categories um/content plus ambiguous audit objects.
7. Ask mode where the user selects only um/content.
8. A name-ambiguous object moves into content after FK analysis.
9. A false-positive content object moves out after procedure analysis.
10. All 842 objects are analyzed while only selected final categories are materialized.
11. Boundary relationships are retained for excluded objects.
12. User-specified nested output path.
13. Malicious bundle path traversal.
14. Interrupted export resumed from checkpoints.
15. No project-local temporary residue.
16. Repeated run updates only managed changed files.
17. Analysis/materialization count or hash mismatch blocks completion.
18. A model proposal with valid sanitized evidence remains suggested until owner resolution.
19. A proposal with invented evidence or an attempted owner assertion is rejected.
20. Catalog/export cancellation is cooperative and idempotent.
21. Expired-artifact cleanup and `507 RUNTIME_STORAGE_FULL` behavior respect active/unexpired jobs.
22. The SQLFluff manifest equation covers exactly the materialized files.
23. Cross-process resume reuses the same protected snapshot masking key.
24. Export status integrity metadata drives fetch verification.
25. Destination-file corruption or omission fails assembled-inventory validation.
26. Agent credentials cannot execute privileged operations without a matching owner grant.
27. Catalog cleanup waits for active/unexpired dependent exports.

Create:
- skills/sql-context-pack/SKILL.md as the single canonical Skill
- skills/sql-context-pack/examples/
- skills/sql-context-pack/references/
- skills/sql-context-pack/scripts/ only when deterministic code is necessary
- fixtures/
- end-to-end tests
- README usage section that links to the required detailed documentation

Run:
- formatting
- linting
- type checking
- unit tests
- integration tests
- end-to-end tests

Do not mark the project complete if any mandatory acceptance test is skipped.
Before stopping, update `CHANGELOG.md` and `docs/implementation-state.md`; keep the current product version unless Section 19.10 requires a corrective patch bump.
At the end, report:
- implemented requirements,
- test evidence,
- known limitations,
- exact commands to install, start the server, run doctor, and invoke the skill.
```

---

## Prompt Chunk 7 — Cross-harness Packaging, Documentation, Versioning, and Release Gate

```text
Continue the existing `sql-context-pack` monorepo.

Split this work across fresh sessions, each reading `docs/implementation-state.md`, `CHANGELOG.md`, and `docs/spec/design-spec-v1.5.md` Sections 1.3, 3.4–3.5, 19, and 21–23:

- Chunk 7A: three-harness packaging, documentation, generated examples, and deterministic conformance simulators.
- Chunk 7B: supported installed-harness smoke tests, final audit, and release gate.

Complete, test, document, and update implementation state for 7A. Keep the current release version (`1.1.0` for this specification) unless a failed review/gate requires the next corrective patch bump in Section 19.10. Chunk 7B runs the two-phase gate without adding a prerelease suffix or changing version merely because the gate passed.

Do not create another repository and do not copy the canonical Skill workflow.

PACKAGE THE SAME CANONICAL SKILL FOR:
- Codex using `.codex-plugin/plugin.json` plus a checked-in config example,
- Claude Code using `.claude-plugin/plugin.json` plus `.mcp.json.example`,
- Gemini CLI using `gemini-extension.json` plus a checked-in settings example.

All packages must reference `skills/sql-context-pack/SKILL.md` at the repository root. Vendor directories may contain only manifests, installation/configuration guidance, and thin compatibility metadata.

DEFAULT CONNECTION
- Owner starts `sqlctx-server` before an agent connects.
- Use owner-started loopback Streamable HTTP MCP at `/mcp`.
- Use the owner-approved bootstrap/configuration command to read agent-scoped connection metadata. Examples may reference `SQLCTX_MCP_URL`, `SQLCTX_API_TOKEN`, or the agent metadata path, but never commit or print actual values. Harness configuration must never reference the separate owner-control credential.
- Keep client-managed STDIO disabled unless the owner explicitly opts in.
- Do not install or manage the harness CLI executables.

CREATE AND COMPLETE:
- README.md
- docs/getting-started.md
- docs/server-operations.md
- docs/command-reference.md
- docs/use-cases.md
- docs/api-and-mcp-examples.md
- docs/security.md
- docs/troubleshooting.md
- docs/harnesses/codex.md
- docs/harnesses/claude-code.md
- docs/harnesses/gemini-cli.md
- CHANGELOG.md

DOCUMENTATION RULES
- Provide exact install, profile, start, health, doctor, MCP-connect, export, validation, update, and invocation commands.
- Use tables for command/use-case studies.
- Provide two or three examples for each applicable topic: bootstrap, profiles, startup, SQLFluff, materialization, output paths, classification, paging/resume, and harness use.
- Include preconditions, expected important output, and next action.
- Use placeholders only; never include realistic secrets.
- Generate API/MCP schemas and examples from shared runtime models where possible.

VERSIONING
- Use one SemVer product version for package, server, CLI, canonical Skill, Codex plugin, Claude plugin, and Gemini extension.
- Start this specification's implementation at `1.1.0` with no prerelease suffix; `1.0.0` remains the archived v1.4 baseline.
- When a failed test/review requires a correction, bump patch once for the corrective iteration, synchronize every version surface, update `CHANGELOG.md`, and restart Phase A.
- Use minor for backward-compatible feature/scope expansion and major for a breaking public contract.
- Keep canonical `output_format_version` independent and bump it only for incompatible bundle/index changes.
- Add a version-consistency test covering pyproject metadata, health output, `SKILL.md` `metadata.version`, and all three manifests.

CONFORMANCE TESTS
Run the same deterministic fixture through Codex, Claude, and Gemini harness adapters/simulators. Verify:
- Skill discovery,
- MCP tool discovery,
- no credential exposure,
- ask/select behavior,
- complete cursor traversal,
- full analysis despite selective output,
- optional model proposal remains non-authoritative,
- consolidated owner resolution,
- identical normalized counts and artifacts,
- final validation.

Before release, run opt-in smoke tests against installed supported versions of Codex, Claude Code, and Gemini CLI. Record exact harness versions and results. A missing required harness smoke test blocks a release unless the release report explicitly marks the build non-releaseable.

PHASE A — PRE-RELEASE GATE ON THE CURRENT VERSION
- formatting passes,
- linting passes,
- type checking passes,
- unit, contract, integration, E2E, and harness tests pass,
- documentation links and examples validate,
- every HTTP/MCP public operation has schema and examples,
- no duplicate Skill workflow exists,
- version consistency passes,
- `.github/workflows/ci.yml` exercises every mandatory implemented phase,
- CHANGELOG contains the current version's non-finalized change record,
- working tree contains no project-local temporary residue.

If Phase A reveals a defect requiring repository changes, bump patch once, record the correction, and rerun all of Phase A.

PHASE B — FINALIZE AND VERIFY THE SAME VERSION
- finalize the current version's date and release entry in `CHANGELOG.md`,
- build the release artifacts without changing the current version,
- rerun cross-surface version consistency,
- rerun package/install and mandatory release smoke tests,
- verify artifact hashes and release documentation,
- update `docs/implementation-state.md` to released only after all checks pass.

If Phase B reveals a defect requiring repository changes, do not release that version: bump patch, reopen Phase A, and repeat. Passing either phase never triggers a version change by itself.

Report:
- why one repository is used,
- final repository tree,
- released version and changelog entry,
- commands for each harness,
- test and smoke-test evidence,
- known limitations.
```

---

# 21. Definition of Done

The project is complete only when:

1. The owner can configure a read-only profile without exposing credentials to the agent.
2. The owner can start one Python server that provides HTTP and MCP.
3. The skill can discover all objects through paginated category-preview and sitemap calls.
4. The skill asks all versus selected categories when the request does not specify a mode.
5. Category selection never reduces full database analysis scope.
6. Pass 1 provides only preliminary category discovery.
7. Pass 2 reclassifies every object using full sanitized relationships and dependencies.
8. Selected output uses final categories and correctly includes/excludes objects that changed category.
9. Selective output preserves boundary nodes and edges for excluded related objects.
10. Every normal request asks for at least ten real sanitized rows per eligible table; exactly the requested count is emitted when available, and every unavoidable shortage is reported without fabrication.
11. SQLFluff is installed once in the selected base host Python's user site when missing and owner-approved, and can be explicitly updated through that same interpreter while no export/format job is active.
12. One broken SQL file does not stop the rest.
13. No formatting is applied to unparsable SQL.
14. No project-local temporary directory remains.
15. Categories `um` and `content` produce the requested direct-folder structure.
16. Unknown categories are escalated with all available category choices and evidence.
17. Relationship, dependency, tag, boundary, and graph-ready indexes are generated.
18. The final validator separately proves analysis completeness and materialization completeness.
19. The final validator proves path safety, content hashes, intentional exclusions, and absence of detected raw secrets.
20. The implementation works with SQL Server, MySQL, MariaDB, Oracle, and PostgreSQL through separate adapters and correct SQLFluff dialect mappings.
21. Reports are honest about partial failures and never claim unsupported certainty.
22. One GitHub monorepo contains the Python core, server, CLI, canonical Skill, tests, documentation, and all three harness packages.
23. Codex, Claude Code, and Gemini CLI use the same canonical `SKILL.md` and pass the same normalized conformance scenario.
24. The default MCP connection targets an owner-started loopback Streamable HTTP service; opt-in STDIO never exposes database credentials to the model.
25. The server never calls a provider model API; harness/model proposals use sanitized evidence and remain non-authoritative until owner resolution.
26. Every public HTTP operation and MCP tool has generated input/output schemas, behavior/error documentation, and representative examples.
27. Operator, command, case-by-case, troubleshooting, security, and per-harness guides exist with two or three examples for each applicable topic.
28. Every product surface starts this v1.5 implementation at `1.1.0` without a prerelease suffix; `1.0.0` remains the archived v1.4 baseline, corrective iterations bump patch and restart the two-phase gate, and passing the gate does not change version.
29. SQLFluff formats only final-materialization files and manifest formatting counters satisfy `format_requested = formatted + parse_failed_preserved + format_failed_preserved = materialized_object_count`.
30. `output_format_version` is the only output-format field name, with `expected_output_format_version` used by validation.
31. Fingerprinted catalog/export list operations rediscover only exact matching jobs and cooperative cancel operations can reach `cancelled`.
32. Completed catalogs and export artifacts default to 24-hour retention under a configurable 5 GiB runtime quota; active and unexpired work is not silently deleted.
33. Request-bound owner-approved delete operations clean catalog/export jobs explicitly.
34. Separate agent and owner credential metadata is owner-only; neither credential appears on stdout, in prompts, or in tool/command-line arguments.
35. HTTP and MCP create operations pass the same idempotency contract tests.
36. Large bundles travel only through `sqlctx export fetch` over HTTP; MCP never returns ZIP/base64 or unrestricted local paths.
37. HTTP and MCP expose the same structured export report.
38. Python 3.11 or newer is enforced and checked-in CI covers the required phases.
39. `docs/spec/design-spec-v1.5.md` matches the authoritative artifact hash and fresh sessions use routed sections plus implementation state.
40. A cross-process resumed snapshot reuses the same protected masking key, and its masking state remains retained through dependent-export lifetime.
41. Every SQLFluff parse/format/version/install/update command uses the explicitly selected absolute host interpreter; update is rejected while an export/format job is active, and no sqlctx path creates or manages a Python environment.
42. Category preview always has `items` plus `page`, and paginated catalog traversal uses cursor-capable MCP tools rather than non-parameterized resources.
43. Catalog/export rediscovery requires matching request, selection, and object-batch fingerprints.
44. No public export request can disable mandatory formatting or sample appending.
45. Completed export status exposes immutable size, bundle hash, manifest hash, host-interpreter fingerprint/version, SQLFluff version, tooling fingerprint, and output format version over HTTP and MCP without exposing the absolute interpreter path.
46. Final validation reopens every assembled managed file locally and compares its relative-path/size/hash inventory with export manifests.
47. An agent token alone cannot delete, confirm classifications, install or update SQLFluff, enable remote mode, or weaken masking; privileged actions consume a matching one-time owner approval.
48. Catalog cleanup is blocked while a dependent export is active or unexpired.
49. Phase A validates the current release version; Phase B finalizes and verifies the same version without a circular changelog/version transition.
50. Missing or unsupported Python is detected by non-Python preflight helpers that provide official platform guidance and perform no installation, elevation, PATH mutation, or environment creation.
51. Tests prove that no virtualenv/venv/conda/pipx/bundled-Python directory or package payload is created under the Skill, target project, or sqlctx runtime store.

---

# 22. Direct Answer: Will the Plugin Install SQLFluff Automatically?

**It can be guaranteed only when executable bootstrap logic is implemented.**

Installing or copying an Agent Skill file by itself does not inherently run `pip install`. Therefore the repository must provide:

```text
- SQLFluff as a pinned package dependency
- sqlctx sqlfluff ensure
- automatic ensure before the first formatting operation
- cross-process install lock
- post-install verification
- clean-machine integration test
- non-Python PowerShell/POSIX preflight and official Python installation guidance
```

With those requirements implemented and process execution/network access permitted:

- SQLFluff is installed into the selected base host Python's user site when missing and owner-approved.
- It is installed only once for the active pinned version.
- Later exports reuse it.
- Updates happen only through an explicit owner command, only while idle, and through the same host interpreter.
- The Skill, target project, and sqlctx runtime store contain no sqlctx-created Python environment or package payload.

If Python >=3.11 is missing, the preflight must return `PYTHON_UNAVAILABLE` with owner installation/verification guidance and must not install Python. If the host blocks package installation or has no network/package source, the system must return `TOOLING_UNAVAILABLE`; it must not pretend that SQLFluff was installed or create a virtual environment to bypass policy. An owner-selected pre-existing virtual/conda environment is verify/execute-only and is never created or mutated by sqlctx.

Precise cross-harness answer:

```text
Plugin/extension file copied only: no install-time guarantee.
Python >=3.11 absent: stop with PYTHON_UNAVAILABLE and official owner installation guidance.
Base host Python available and first Skill run approved: ensure pinned SQLFluff once in its user site before formatting.
Owner-selected existing environment: verify/execute only; never install or update it automatically.
Normal later run: reuse; do not reinstall.
Update: explicit owner command only, while idle, through the same host interpreter.
Virtual environment creation by sqlctx/Skill: forbidden.
```

---

# 23. Direct Answer: How Many GitHub Repositories?

Create **one GitHub repository**:

```text
sql-context-pack
```

Keep the Python server, HTTP/MCP interfaces, CLI, canonical Skill, Codex plugin manifest, Claude Code plugin manifest, Gemini CLI extension manifest, tests, fixtures, examples, documentation, and changelog in that monorepo.

Do not create these as separate repositories in v1:

```text
sqlctx-server
sqlctx-skill
sqlctx-codex
sqlctx-claude
sqlctx-gemini
```

They share one security boundary, one protocol contract, one canonical workflow, and one release version. Split later only when a component obtains a genuinely independent owner, security boundary, or release cadence.

---

# 24. Final Classification Decision

The recommended default behavior is:

```yaml
classification:
  strategy: two_pass

selection:
  mode: ask

selective_output:
  excluded_dependencies: index_only_boundary_metadata
```

Runtime behavior:

```text
1. Discover every permitted object name.
2. Build a preliminary category preview.
3. Ask: all categories or selected categories?
4. Record selected category names.
5. Dump and sanitize every permitted object.
6. Analyze all relationships and routine dependencies.
7. Run deterministic Pass 2 classification.
8. Let the active harness/model optionally submit sanitized non-authoritative proposals.
9. Ask the owner once for remaining ambiguous business decisions.
10. Apply selected category names to final categories.
11. Materialize only included objects.
12. Preserve excluded connected objects as boundary metadata.
```

This design is intentionally more expensive than filtering from names alone, but it prevents the more serious failure mode: silently omitting SQL objects whose names do not reveal their real business context.

---

# 25. Official Sources of Trust

- SQLFluff dialect reference: https://docs.sqlfluff.com/en/stable/reference/dialects.html
- SQLFluff installation/getting started: https://docs.sqlfluff.com/en/stable/gettingstarted.html
- SQLFluff CLI production behavior and exit codes: https://docs.sqlfluff.com/en/latest/production/cli_use.html
- SQLFluff troubleshooting/parsing errors: https://docs.sqlfluff.com/en/stable/guides/troubleshooting/how_to.html
- SQLFluff default configuration and `fix_even_unparsable`: https://docs.sqlfluff.com/en/stable/configuration/default_configuration.html
- Official Python downloads: https://www.python.org/downloads/
- Using Python on Windows: https://docs.python.org/3/using/windows.html
- Python Packaging User Guide — installing packages and user installs: https://packaging.python.org/en/latest/tutorials/installing-packages/
- MCP specification — tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP specification — resources: https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- MCP authorization: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- Official MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Open Agent Skills specification: https://agentskills.io/specification
- Codex Agent Skills: https://learn.chatgpt.com/docs/build-skills
- Codex MCP configuration: https://learn.chatgpt.com/docs/extend/mcp
- Claude Code Agent Skills: https://code.claude.com/docs/en/slash-commands
- Claude Code plugin reference: https://code.claude.com/docs/en/plugins-reference
- Claude Code MCP: https://code.claude.com/docs/en/mcp
- Gemini CLI Agent Skills: https://geminicli.com/docs/cli/using-agent-skills/
- Gemini CLI extension reference: https://geminicli.com/docs/extensions/reference/

At implementation/release time, re-verify the current stable harness, MCP specification, MCP Python SDK, and SQLFluff versions. Pin tested stable versions; do not adopt an alpha, beta, draft, or release candidate merely because it is newer.

---

# 26. Raw Requirement Traceability and v1.2 Gap Closure

| Original raw requirement | Historical assessment through v1.2 | Current v1.5 authoritative coverage |
|---|---|---|
| Detailed but non-drifting implementation prompt; chunk when large | covered, but release/harness work was not chunked | Section 20, including new Chunk 7 |
| Secure owner-managed DB user/password; owner starts service | mostly covered; default MCP process lifecycle was ambiguous | Sections 4.1–4.5 and 12.5 |
| Universal repository/product name | covered | Sections 1.1–1.3 |
| Dump table DDL and stored procedures, format, classify | covered | Sections 2, 7, 9, 10, and 13 |
| `um`/`content` direct folder structure | covered | Section 15.3 |
| Output path inferred from user language | covered | Sections 15.1 and 18.2–18.3 |
| At least 10 representative rows per table | ambiguous conflict: v1.1 used “up to 10” and allowed lower profile limits | Sections 8.1, 17.1, Chunk 3, and Definition of Done item 10 |
| Python/Node trade-off and Web/MCP service design | covered; Python selected | Sections 3, 11, and 12 |
| Function/API name, URL, HTTP behavior, input/output, examples | HTTP mostly covered; MCP per-tool contracts and examples were incomplete | Sections 11.20 and 12.5 |
| Sensitive cleansing before model input/output | covered | Section 5 |
| 100–1000+ objects, paging, sitemap, batching, resumability | covered | Sections 10 and 17 |
| Skill uses service intelligently and never guesses categories | covered | Sections 13 and 18 |
| Tags/indexes, ER cardinality, graph/tree-ready future phase | covered | Sections 14 and 16 |
| SQLFluff auto-ensure once and explicit update | covered, but plugin-install timing needed precision | Sections 9 and 22 |
| SQL Server/MySQL/MariaDB/Oracle/PostgreSQL dialect mapping | covered and corrects `psql` to SQLFluff `postgres` | Sections 6, 7, and 19.7 |
| One broken file must not stop directory-wide formatting | covered | Section 9.6–9.8 |
| No project-local temporary residue | covered | Sections 9.9 and 15.2 |
| Ask/all/selected and two-pass full analysis | covered | Sections 10 and 13 |
| Model updates changelog and increases version | v1.2 required a release bump for every repository edit, which caused build-version drift | Sections 19.10, Chunk 0–7, and Definition of Done item 28 use current `1.1.0`; corrective iterations bump patch and rerun the gate |
| MCP and Skill usage guides case by case | only a README usage mention; incomplete | Sections 19.8 and Chunk 7 |
| Command studies/examples in tables, two or three cases when applicable | missing | Section 19.8 and Chunk 7 |
| Codex, Claude, and Gemini harness/model support | missing | Sections 1.3, 3.4–3.5, 19.9–19.11, and Chunk 7 |

Appended raw requirements 6–23 close these verified v1.2 gaps:

| Appended raw requirement | v1.2 finding | Current v1.5 closure |
|---|---|---|
| 6 SQLFluff stage/accounting | stage undefined; `820 + 22` did not equal 214 materialized files | Sections 8.4, 9.7, 15.5; format only final materialization and require `210 + 4 + 0 = 214` in the example |
| 7 report symmetry | MCP report resource had no HTTP peer | Sections 11.16a, 11.20, 12.2, 12.5 |
| 8 canonical output version | three names could become separate fields | Sections 11.17, 15.5, 17.5, 19.10 use only `output_format_version` / `expected_output_format_version` |
| 9 HMAC alias mapping | sequence-looking examples did not define encoding/collision/resume | Section 5.4–5.5 defines Base32 digest aliases and protected per-snapshot registry |
| 10 pagination/workflow parity | classification example lacked page envelope; Chunk 6 omitted manifest step | Sections 11.11, 18.3, and Chunk 6 use paginated envelopes and matching 38-step flows |
| 11 list/cancel | cancelled state existed without callable operations; jobs could not be rediscovered | Sections 11, 12, 17, 18 and Chunk 5 add paginated list and cooperative cancel operations |
| 12 retention/quota/delete | runtime snapshot/artifact lifetime was unbounded | Sections 11.19 and 17.6 define 24-hour defaults, 5 GiB quota, 507, and owner delete |
| 13 token handoff | random token transport to harness/CLI was unspecified | Sections 4.5, 12.5 and Chunks 2/5 define owner-only metadata and no token stdout/arguments |
| 14 idempotency | mentioned but no field/header semantics | Sections 11.5, 11.13, 11.20, 12.5 define required HTTP header/MCP field and conflict behavior |
| 15 bundle transfer | MCP binary/base64 versus HTTP use was ambiguous | Sections 11.15 and 12.2 select `sqlctx export fetch` over HTTP and remove MCP bundle bytes |
| 16 Python/CI | no minimum Python and no chunk created CI | Sections 1.3, 19.1, 19.11 and Chunk 1 require Python >=3.11 and `.github/workflows/ci.yml` |
| 17 build/release versions | every-edit release bump could drift before release | Section 19.10 and Chunks 0–7 use current `1.1.0`, correction-level patch bumps, and a two-phase same-version gate |
| 18 authoritative spec routing | Chunk 0 paraphrase could drift from the design contract | Sections 1.3, 19.12, 20 require byte-for-byte v1.5, hash, section routes, and implementation state |
| 19 fresh sessions/sub-chunks | same long session risked context overflow | Section 20 uses fresh sessions and splits adapters/harness release work |
| 20 source precedence | old versions could conflict during implementation | Sections 19.12 and 20 make v1.5 the only implementation source of truth |
| 21 cut optional scope | relationship-aware sampling and dependency direct/closure were not in raw v1 | Sections 8 and 10.3 plus Chunks 3/4/6 remove them from v1 |
| 22 model attribution | no verifiable author metadata exists | Section 19.12 forbids authorship guesses without metadata |
| 23 token estimate | token count is tokenizer-dependent | Sections 19.12 and 20 use spec-in-repo/routing and require measurement with target tokenizer |

The statement that all four analysis failures were necessarily extraction failures was not provable from v1.2 because the failure type was not specified. v1.3 therefore fixes the demonstrable defect—undefined SQLFluff scope and inconsistent counters—without inventing a cause for those failures.

The v1.5 raw additions supersede the v1.4 managed-runtime interpretation without deleting its history:

| Appended raw requirement | v1.5 closure |
|---|---|
| 38 host Python; no Skill-owned environment | Sections 9.1–9.2, 19.1–19.2, Chunk 0–2, Definition of Done 41/51, and Section 28.1 require the configured absolute host interpreter or `sys.executable` and forbid sqlctx-created virtualenv/venv/conda/pipx/bundled Python |
| 39 explicit SQLFluff command/install/update | Sections 9.3–9.8, 11.13–11.14, 15.5, and 17.5 use `<host-python> -m ...`, owner-approved base-interpreter user-site install, idle-only same-interpreter update/rollback, and tooling fingerprints instead of runtime IDs |
| 40 Python unavailable guidance/version | Sections 9.10, 19.1, 19.10–19.13, Chunks 0–2/7, Definition of Done 28/50, and Section 28.1 require non-Python platform preflight, official install guidance, specification 1.5, and product 1.1.0 |

All raw requirements now have a normative section, implementation-chunk instruction, or acceptance criterion. The original raw text is preserved, and its additions narrow v1 scope where stated.

---

# 27. Resolved Trade-offs for v1.5

| Decision | Selected v1.5 behavior | Rejected alternative and reason |
|---|---|---|
| GitHub repositories | one `sql-context-pack` monorepo | multiple repos create version/contract drift without a v1 ownership boundary |
| implementation language | Python | Node.js still needs Python/SQLFluff and adds runtime coordination |
| default MCP lifecycle | owner-started loopback Streamable HTTP | default client-spawned STDIO conflicts with the owner-runs-first requirement; STDIO remains opt-in |
| provider integration | deterministic provider-neutral server plus optional harness/model proposals | server-side Codex/Claude/Gemini API integrations add provider credentials and duplicate harness capability |
| three-harness Skill packaging | one canonical open-format `SKILL.md` plus three manifests/configs | three copied Skills would drift |
| ten-row wording | request at least 10; export the requested real rows when available; explicitly report unavoidable shortage | duplicating/fabricating rows damages truth; silently allowing requests below 10 violates the stated target |
| SQLFluff bootstrap | selected host Python plus first-use owner-approved user-site `ensure`, cached after success | creating a Skill-owned virtual environment violates the host-Python requirement and obscures which interpreter runs SQLFluff |
| Python unavailable | non-Python preflight stops with official owner installation guidance | automatically installing Python, requesting elevation, or mutating PATH exceeds Skill authority |
| owner-selected existing environment | verify and execute only | automatic install/update/repair/delete would make sqlctx the environment owner |
| SQLFluff stage | format only final-materialization SQL after Pass 2/owner resolution | formatting every analyzed object adds cost without helping relationship analysis |
| dependency materialization | fixed index-only boundary metadata | direct/closure modes are outside the raw v1 scope and can erase selective-output savings |
| bundle transport | deterministic CLI fetch over authenticated loopback HTTP | MCP ZIP/base64 inflates payloads and risks message limits; unrestricted runtime paths violate the boundary |
| runtime lifecycle | 24-hour completed retention, 5 GiB quota, explicit delete | unbounded storage and silent eviction are unsafe |
| implementation context | byte-for-byte v1.5 in repo, routed fresh sessions | a single long context or agent-written contract summary invites drift |
| product version | use current `1.1.0`; v1.4/`1.0.0` remains archived; bump patch for later corrective iterations | prerelease suffixes were explicitly rejected for this build-and-release workflow |
| dependency/spec freshness | re-verify and pin tested stable versions at release | automatically adopting prerelease or `latest` can break reproducibility |

These defaults are implementation-ready. Changing any of them is a product trade-off that must update this specification, acceptance tests, version, and changelog before implementation continues.

---

# 28. Final Review Closure and Cut-off Decision

| Final review blocker | Resolution carried into v1.5 |
|---|---|
| resumed masking aliases could drift | persist/encrypt or deterministically derive the same snapshot key; fail explicitly if secure cross-process resume is unavailable |
| SQLFluff update could select one interpreter but execute another | resolve one absolute host interpreter, pin immutable interpreter/tooling fingerprints per export, invoke it explicitly, and reject updates while work is active |
| category-preview pagination shapes disagreed | standardize on `items` plus `page` and null-cursor traversal |
| rediscovery could resume a different request | require canonical request, selection, and object-batch fingerprints |
| clients could disable mandatory export stages | remove the switches and reject explicit false compatibility input |
| completed export status lacked integrity data | return immutable size, bundle/manifest hashes, host-interpreter/tooling fingerprints, SQLFluff version, and output format version over HTTP/MCP |
| server-only validation could miss damaged assembled files | local CLI reopens destination files and submits the complete managed inventory for manifest comparison |
| paginated MCP resources had no cursor/view parameters | remove catalog resources and use cursor-capable tools; retain only small manifest/report resources |
| harness token could invoke owner operations | separate agent/owner credentials and enforce single-use request-bound approval |
| catalog cleanup could break dependent exports | dependency-pin catalog state and cleanup exports before eligible catalogs |
| release gate required a release entry before allowing it | Phase A validates the current version; Phase B finalizes and verifies that same version |

Cut-off decision:

```text
Authoritative specification: 1.5
Status: implementation-ready cut-off
Current product version: 1.1.0
Prerelease suffix: forbidden for this workflow
Correction after failed review/test: bump patch and restart Phase A
Backward-compatible feature/scope expansion: bump minor
Breaking public contract: bump major
```

### 28.1 v1.5 host-Python correction

The v1.4 archive is not edited. v1.5 supersedes it for implementation and makes these rules normative:

1. Use Python already installed on the user's machine: an owner-configured absolute executable or `sys.executable`.
2. Invoke SQLFluff only as `<host-python> -m sqlfluff ...`; do not resolve a different interpreter from PATH later.
3. Never create or manage virtualenv/venv, conda, pipx, bundled Python, or a Python package directory inside the Skill, target project, or sqlctx runtime store.
4. A supported base interpreter may receive an owner-approved pinned SQLFluff user-site install; an owner-selected pre-existing environment is verify/execute-only.
5. A missing Python produces `PYTHON_UNAVAILABLE` with official installation/verification guidance from non-Python helpers; sqlctx does not install Python.
6. SQLFluff update is idle-only and uses the same interpreter; a request during active work returns `409 TOOLING_BUSY`.
7. The global distribution and owner-setup product version is `1.1.0`; no `-dev.N` or other prerelease suffix is introduced.

---

# 29. Final v1.5 Global Distribution and Owner Setup Contract

Sections 29–32 are the final positive end-state contract for global distribution, setup, profile
connectivity, launch, and operational visibility. They take precedence over earlier lifecycle,
packaging, profile-bootstrap, and product-version text within those scopes. The canonical source is
`https://github.com/orchex006/sql-context-pack`; specification `1.5` is the final implementation
cut-off and product version is `1.1.0`. Output format version remains `1`.

## 29.1 One canonical repository and Skill

The repository contains one Python application core, one canonical
`skills/sql-context-pack/SKILL.md`, provider manifests, global installation scripts, tests, docs,
and release metadata. Codex distribution uses a personal-marketplace plugin whose installed copy
contains the canonical Skill and plugin manifest. The plugin performs Skill discovery. The
owner-launched CLI supplies the runtime MCP connection to the Codex child process.

The default Windows owner installation is:

```powershell
git clone https://github.com/orchex006/sql-context-pack.git
cd sql-context-pack
.\install.ps1
```

`install.ps1` performs the host-Python preflight, installs the package and pinned SQLFluff into the
selected interpreter's user site, resolves launchers, installs/updates the personal marketplace
plugin, validates the Skill/plugin, and starts secure profile configuration when no profile exists.
Ruff remains an optional development/CI dependency and is not installed by the normal owner flow.

## 29.2 Secure profile setup and discovery

Interactive setup runs through the selected host Python:

```powershell
py -3 -m sqlctx.cli.configure
py -3 -m sqlctx.cli profile list
py -3 -m sqlctx.cli profile test agrimap-dev
```

The wizard asks for profile name, database engine, host or instance, TCP port, database/service,
allowed schemas, read-only username, and a hidden confirmed password. It merges `profiles.yaml`,
`categories.yaml`, and `category-overrides.yaml` under the platform user config directory. YAML
contains only the safe profile policy and `credential_ref`; connection values are encrypted under
the owner-only runtime directory. Environment-reference profiles remain available for unattended
owner-managed deployments.
Interactive host input is literal: SQL Server named-instance forms are `host\instance` or
`host\instance,port` and must not include surrounding shell quote characters.

`profile list` returns exact usable names, engine, schemas, object types, sampling policy, and
readiness without resolving connection values. The wizard and `profile test` perform a bounded
connection test and return stable sanitized codes for driver availability, host/instance/port,
TLS trust, and login authentication.

## 29.3 SQL Server driver resolution

SQL Server connections are DSN-less. The selected host Python imports `pyodbc`, reads its installed
driver list, selects `ODBC Driver 18 for SQL Server` when available, and otherwise selects
`ODBC Driver 17 for SQL Server`. If neither is present, the safe diagnostic reports supported and
installed driver names. Connection construction uses explicit server/port/database/read-only login,
encryption, certificate verification, and bounded timeout. Diagnostics never include the connection
string or resolved profile values.

## 29.4 One-command owner launch

After installation and initial profile setup, normal operation is:

```powershell
py -3 -m sqlctx.cli launch --harness codex --profile agrimap-dev
```

This explicit owner command:

1. lists safe profiles and enters secure setup when none exists;
2. selects the exact `--profile`, or selects automatically only when exactly one profile is ready;
3. performs the bounded profile connection test and opens no service/harness until it passes;
4. starts `sqlctx.server.http.app` on `127.0.0.1:8765` as its child when the service is absent;
5. waits for loopback readiness and protected connection metadata;
6. launches Codex with an absolute Streamable HTTP MCP URL and
   `bearer_token_env_var=SQLCTX_API_TOKEN` supplied only to the child environment;
7. preserves an already-running owner service; and
8. stops only the service child it created when the harness exits.

The installed plugin does not persist a bearer or unresolved MCP URL. The effective registration is
inspectable with `py -3 -m sqlctx.cli harness mcp-list --harness codex`, which shows
`sql-context-pack`, the loopback URL, enabled status, and Bearer-token authentication.

---

# 30. Sanitized Operational Audit

Every MCP tool call records one protected owner-local event containing:

- random event ID and UTC timestamp;
- transport and exact operation name;
- pseudonymous caller derived from the agent bearer;
- succeeded/failed outcome, duration milliseconds, and stable error code when applicable.

Events exclude tool arguments, SQL text, connection values, credentials, tokens, samples, and raw
database metadata. The Python server emits the same sanitized event through `sqlctx.audit` at INFO
level and stores each event with owner-only permissions under the runtime audit directory. Owners
inspect recent activity with:

```powershell
py -3 -m sqlctx.cli audit tail --limit 50
```

HTTP access logs continue to show loopback method/path/status, while MCP audit events provide the
tool-level operation identity that transport logs cannot infer.

---

# 31. Final v1.5 Implementation Routing

A fresh implementation session uses this complete file as the source of truth and builds the
end-state directly. It does not replay review conversations or derive requirements from issue
history. Implementation order is:

1. typed Python core, safe profiles, runtime protection, and host-Python SQLFluff;
2. five database adapters with SQL Server DSN-less driver discovery;
3. catalog, classification, indexing, export, integrity validation, and retention;
4. shared HTTP/MCP facade with authenticated Streamable HTTP and sanitized operation audit;
5. canonical Skill and three harness manifests;
6. root installer, personal-marketplace plugin, profile wizard, safe discovery/test commands, and
   one-command launch;
7. operator docs, issue/resolution register, generated contracts, full tests, package/plugin smoke,
   changelog, and release consistency.

Implementation sessions read routed sections from this checked-in specification. The final build
uses product `1.1.0`, specification `1.5`, output format `1`, Python `>=3.11`, and SQLFluff `4.2.2`.

---

# 32. Final v1.5 Acceptance Additions

The cut-off is complete when:

1. `install.ps1` takes a clean Windows owner from repository checkout to installed package,
   marketplace Skill, validated SQLFluff, and first secure profile setup.
2. `profile list` exposes an exact copyable ready profile name without connection values.
3. `profile test` and the wizard select SQL Server ODBC Driver 18 or 17 from the installed list and
   return actionable sanitized connectivity codes.
4. `launch --harness codex` starts the service when needed, supplies authenticated ephemeral MCP
   configuration, opens Codex, and cleans up only its owned child service.
5. Effective MCP listing shows the SQL Context Pack Streamable HTTP endpoint with Bearer-token auth.
6. MCP tool activity is visible in sanitized server logs and `audit tail` without arguments or
   secrets.
7. The global plugin, direct fallback rules, Python package, SQLFluff invocation, profile storage,
   output integrity, and three-harness contracts pass the full release gate.
8. `CHANGELOG.md`, install/getting-started/troubleshooting docs, generated schemas, version surfaces,
   package artifacts, marketplace metadata, and canonical repository URL agree on the release.

---

# 33. Final v1.5 Global Agent Marketplace Contract

This section completes the global-distribution contract within specification `1.5`. It is
normative together with Sections 29–32 and contains the complete install, update, status, removal,
and discovery behavior needed to reproduce the released project without a separate delta
specification.

## 33.1 Distribution modes and resolved user layout

An owner can install SQL Context Pack once at user scope and invoke `$sql-context-pack` from a new
Agent session without opening the repository. Both supported Codex distribution modes use the one
canonical authored workflow at `skills/sql-context-pack/SKILL.md`:

1. **Personal marketplace plugin — default and recommended.** Install a local Skill-only plugin,
   register it in the owner's personal marketplace, and install `sql-context-pack@personal`.
2. **Direct global Skill — explicit fallback only.** Install under the Codex global Skill directory
   only when marketplace installation is unavailable or the owner explicitly selects the fallback.

The modes are mutually exclusive. Installation fails safely when activating one would leave the
other active and create duplicate Skill discovery. Claude Code and Gemini CLI use their checked-in
provider manifests with the same canonical Skill; no provider-specific workflow copy is authored.

Logical destinations are resolved from the actual user home and never from a hard-coded username:

```text
~/.agents/plugins/marketplace.json
~/plugins/sql-context-pack/
├── .codex-plugin/plugin.json
└── skills/sql-context-pack/

~/.codex/skills/sql-context-pack/   # fallback mode only
```

The personal marketplace is named `personal`. Its SQL Context Pack entry is:

```json
{
  "name": "sql-context-pack",
  "source": {"source": "local", "path": "./plugins/sql-context-pack"},
  "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
  "category": "Developer Tools"
}
```

Install, update, and removal preserve the marketplace root, any existing
`interface.displayName`, unrelated entries, and their order. The product entry is appended when
absent; unrelated entries are never rewritten or removed.

## 33.2 Canonical copy, validation, and integrity

Marketplace and fallback directories are installed artifacts, never additional sources of truth.
Installation copies the canonical Skill directory byte-for-byte, including its direct `agents/`,
`references/`, `scripts/`, and `examples/` resources, plus the checked-in Skill-only Codex plugin
manifest when plugin mode is selected.

Before replacing an installed artifact, the installer stages it under OS temporary storage and
validates destination boundaries, manifest structure, Skill frontmatter, product version, and a
deterministic content inventory. It then replaces only the exact SQL Context Pack destination. It
does not create project-local staging directories, Python environments, or partial installed
trees.

- Same version and same inventory is an idempotent no-op except for a safe registration check.
- Same version and different inventory fails closed during ordinary install; an explicit update
  from the canonical repository source may replace it after validation.
- Update reuses the selected distribution mode and refreshes Agent discovery.
- Status reports source version, installed mode/version, marketplace registration, Codex
  installed/enabled state, and inventory/hash match without resolving secrets.
- Removal requires an explicit owner command and deletes only the selected SQL Context Pack
  artifact and its own marketplace entry. Startup, Skill invocation, and server execution never
  remove global artifacts.

After install or update, the owner is told to start a new Agent thread so discovery does not rely
on stale session context. Plugin installation is verified with the installed Codex CLI; fallback
installation is verified from the global Skill discovery files.

## 33.3 Package, PATH, and platform behavior

The explicit global installer uses the selected CPython `>=3.11` for
`<host-python> -m pip install --user <source-or-artifact>` and for every
`<host-python> -m sqlfluff` invocation. Normal installation includes pinned SQLFluff `4.2.2` and
does not install the development extra or Ruff. Windows, macOS, and Linux helpers follow the same
host-Python and owner-scope rules.

The installer resolves the selected interpreter's user `Scripts` or `bin` directory. It may add
only that directory to the current-user PATH after explicit owner invocation; it never changes
system PATH or requests administrator rights. It smoke-tests the `sqlctx` and `sqlctx-server`
entry points. When the current shell has stale PATH state, diagnostics report that a new terminal
is required and provide both the resolved absolute launcher and
`<host-python> -m sqlctx.server.http.app` fallbacks.

Global installation never installs Python, starts the database service or MCP server, opens a
remote listener, creates/manages a virtual environment, or persists a runtime bearer. Missing
Python returns `PYTHON_UNAVAILABLE` with owner-driven platform installation and verification
guidance.

## 33.4 Skill-only plugin and protected runtime MCP

The installed plugin performs Skill discovery only. It contains no MCP auto-start registration,
unresolved `${SQLCTX_MCP_URL}`, relative MCP URL, custom bearer header placeholder, or secret. The
repository root `.mcp.json` likewise contains no auto-registered SQL Context Pack server.

The owner command in Section 29.4 supplies an ephemeral absolute loopback URL and
`bearer_token_env_var=SQLCTX_API_TOKEN` to the Codex child process. The bearer exists only in that
child environment and never appears in command arguments, plugin/marketplace files, persistent
Codex configuration, logs, or prompts. A plain persistent `codex mcp list` may therefore omit SQL
Context Pack; `sqlctx harness mcp-list --harness codex` is the authoritative effective-session
compatibility check.

Global discovery grants no additional authority over Python, SQLFluff, database credentials,
owner approvals, project files, or server lifecycle. The Skill must still check capabilities and
safe profiles, traverse every cursor, preserve two-pass classification, and use the protected CLI
for binary bundle transfer.

## 33.5 Complete marketplace acceptance

In addition to Section 32, the release gate proves all of the following in isolated user homes and
installed-product smoke tests:

1. A clean profile creates the personal marketplace without manual JSON editing.
2. Existing personal marketplace metadata and unrelated entries survive install, update, and
   removal unchanged.
3. `sql-context-pack@personal` is installed and enabled with the required policy and category.
4. The installed plugin contains the canonical Skill and a valid Skill-only manifest, with no MCP
   auto-start entry.
5. Direct global Skill fallback succeeds only while plugin mode is absent.
6. Same-version installation is idempotent; explicit update refreshes changed canonical content;
   status detects inventory drift; removal is product-scoped and explicit.
7. No token, credential, resolved database value, user-specific absolute path, or runtime secret is
   written into repository, marketplace, plugin, or fallback Skill files.
8. A new Agent thread discovers and invokes the canonical Skill globally.
9. Codex, Claude Code, and Gemini manifests resolve to product `1.1.0`, output format `1`, and the
   same canonical workflow.
10. Unit, contract, integration, E2E, harness, isolated global-install simulation, Skill/plugin
    validation, package build, installed-wheel smoke, and installed-plugin smoke all pass.
