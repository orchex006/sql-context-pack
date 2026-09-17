# Changelog

Release history. For how to use the product, start with [Getting Started](docs/getting-started.md).

## 2.1.0 — 2026-09-17

First released version. The repository history begins here, and every earlier internal
iteration (1.0.3 through 1.6.0) is folded into this entry as the dated development record
below. Those versions were never published as releases.

Dates for the 1.1.0–1.3.0 entries, which were recorded as "Unreleased", are taken from the
commit dates of the work they describe rather than invented.

---

### 2026-09-17 — Release 2.1.0

**Security — Query Data read path**

- Reject `FOR XML` and `FOR JSON`. They collapse an unbounded number of rows into a single
  cell, which escapes the `max_rows` bound entirely, and their masking only held because the
  collapsed column count happened to match the projection count. A runtime test showed a
  single benign-named projection emitting the blob unmasked.
- Reject table and query hints: `TABLOCKX`, `UPDLOCK`, `HOLDLOCK`, `NOLOCK`, `INDEX(...)`,
  and `OPTION (...)`. Each needs only SELECT permission, so `assert_query_read_only` cannot
  prove them safe, and they allow exclusive locks on live tables, uncommitted reads, and
  unbounded recursion.
- Reject non-allowlisted bare functions. Parenthesis-free calls parse as `bare_function`
  rather than `function` and previously skipped the allowlist entirely, exposing
  `CURRENT_USER`, `SYSTEM_USER`, `SESSION_USER` and `USER`.
- Confirmed already-closed before this release: INSERT, UPDATE, DELETE, TRUNCATE, DROP,
  ALTER, MERGE, CREATE, EXEC, `sp_executesql`, OPENROWSET, OPENQUERY, OPENDATASOURCE,
  stacked statements, comments, and cross-database references.

**Context index**

- Synchronize the full discovered inventory on every export instead of only the objects that
  reached a confirmed context. `ContextIndexSyncRequest.mode` separates machine-derived
  `inventory` writes, which need no approval and can neither deactivate a row nor overwrite
  an `OWNER` classification, from supervised `owner` writes, which stay approval-gated.
- Record objects that cannot be classified as `unresolved` rather than omitting them, and
  hand the owner one consolidated list of every unresolved object with a suggested context
  and its evidence, explicitly marked as a suggestion.
- Return `METADATA_CONTEXT_WRITE_SCOPE_REQUIRED` with the exact owner command in
  `details.owner_command` instead of a bare 403.
- Fail with `METADATA_CONTEXT_ENGINE_UNSUPPORTED` on PostgreSQL, MySQL, MariaDB and Oracle
  instead of raising `AttributeError`. Only the SQL Server adapter implements the
  metadata-context protocol, and inventory sync now runs on every export, so the unguarded
  path would have surfaced an internal error on every export against those engines.

**Hosts**

- Add `agy` (Antigravity) as a first-class host alongside `gemini`. `--host` accepts
  `codex|claude|gemini|agy`.
- Install Antigravity by file placement under `~/.gemini/config/skills/` plus a merge into
  `~/.gemini/config/mcp_config.json`, because it has no plugin or extension CLI. The merge is
  additive in both directions: other MCP servers survive install, update and removal.
- Accept only the skill layout for Antigravity; the plugin layout would bury `SKILL.md` one
  directory below where it looks.

**Service and updates**

- Move service supervision into `sqlctx.service.manager` so it is reachable from an installed
  package, and expose `sqlctx service status|start|stop|install|remove`.
- Support Windows as a real platform rather than a delegation stub: the SCM service when one
  is registered, and the portable pidfile supervisor otherwise, so an owner without
  Administrator rights still gets one shared service.
- Add `install.sh` for POSIX hosts so `doctor update` works off Windows.
- Add `--version <x.y.z>` to pin an update to an exact released tag. An unknown version stops
  and reports the available tags rather than falling back to the latest release.

**Repository**

- Commit source the repository had never received. `query_data/lineage.py` and
  `security/transport.py` are imported at runtime but existed only in the working tree, so a
  clean clone failed on import and `pip install` from the repository could not produce a
  working package. The 1.5.0 lineage masking and the 1.6.0 declaration-boundary fix were
  likewise uncommitted, meaning the published code still contained the masking bypass the
  1.5.0 notes described as closed.
- Rewrite `scripts/validate_manifests.py`, which had drifted to a hardcoded `VERSION =
  "1.3.0"` while every shipped surface was at 1.6.0. It and the version contract test now
  derive the expected version from `_version.py` and cover the Claude and Antigravity
  marketplaces.
- Recompute the `design-spec-v1.26` SHA-256 sidecar from canonical LF bytes; it alone had
  been generated from a CRLF working copy, against the repository's own `eol=lf` policy.
- Repoint the Antigravity marketplace at `orchex006/sql-context-pack`, which still referenced
  the pre-transfer repository.
- Remove `.agrimap-agent/` from published history and ignore it.

**Workflow and documentation**

- Add `AGENTS.md` covering `feature/*` and `hotfix/*` branches, annotated `v<x.y.z>` tags, the
  version surfaces that must agree before tagging, and the steps required whenever `skills/`
  changes.
- Add `docs/knowledge/` with repair and installation procedures for the MCP service, SQLFluff
  and the four hosts.
- Rewrite the documentation set in English.

---

### 2026-09-16 — SQL Server routine analysis fix (1.6.0)

- Fix a regression introduced in 1.3.0: declaration canonicalization rejected any routine
  whose definition began with a comment, so every stored procedure or function carrying the
  ordinary SSMS banner failed extraction and vanished from sitemap views and materialization
  plans. On one 590-routine schema, 530 routines failed before the fix and 0 after.
- Locate the declaration boundary past the byte-order mark, whitespace, `--` line comments and
  nested `/* */` block comments, preserving the comment prefix verbatim. Declaration keywords
  found only inside a comment or string are still rejected, and an unterminated comment fails
  closed.
- Rewrite only the declaration keywords. Across all 590 live routines the canonicalized text
  is byte-identical to the source apart from those keywords, so `OUTPUT` parameter direction,
  `@@PROCID` and the routine body are preserved. `OUTPUT`, `@@PROCID` and scalar functions
  were never the cause; 36 passing procedures already used `OUTPUT` and 40 already used
  `@@PROCID`.
- Report analysis failures per object. `CatalogStatus` gains `failures[]` with object id,
  object type, stage (`analysis`, `sample`, `dependencies`), a stable error code and a
  sanitized, length-bounded message that excludes credentials, raw SQL bodies and owner
  absolute paths. It is projected through MCP, HTTP and the export manifest as
  `export.analysis_failures[]`. Failed objects are never auto-excluded.

---

### 2026-09-14 — Query Data lineage, transport and service account (1.5.0)

- Preserve source sensitivity through Query Data aliases and expressions, and conservatively
  redact unresolved lineage, complex wildcard renaming and JSON extraction in both bounded and
  streamed output.
- Restrict bearer transport to validated IPv4 loopback endpoints, and disable proxy
  environment variables and redirects while preserving custom local ports and existing MCP
  commands.
- Replace LocalSystem with a dedicated virtual Windows Service account, a protected CPython
  copy, exact application and configuration ACLs, and scoped registered-folder access, with
  migration rollback retained.
- Add `doctor status/version/check/update`, optional online release checks, host selection and
  verified package/plugin/service upgrade results, preserving the existing `doctor --mcp`
  readiness probe.
- Validate the update remote, branch and worktree; pin CI actions; and provide a solo-owner
  main ruleset for explicit GitHub activation. A checked-in ruleset is not proof that remote
  protection is enabled.

---

### 2026-07-29 — Context index, folder classification and routine deployment (1.3.0)

- Add the one-table SQL Server context index `[agrimap_app].[DB_METADATA_CONTEXT]` with typed
  sync/list/resolve contracts, owner-value precedence, explicit numeric actor IDs, paginated
  filtering, profile write scope, approval gates, and hash-checked generation plans.
- Add registered-folder scan, dry-run classification plans, separate-output atomic apply, and
  approval-gated in-place apply with identity, path, link, collision and content-hash checks.
- Add immutable single-file and recursive-folder routine deployment plans. SQL Server
  procedures and functions use validated `CREATE OR ALTER` declarations; apply rechecks file
  identity, content and current database fingerprints, requires explicit routine write scope
  and owner approval, and fails closed on unsupported engines without DROP/recreate.
- Add versioned managed SQL headers and complete all-mode materialization. Profile-allowed
  TABLE, PROCEDURE and FUNCTION definitions that cannot be classified are retained under
  `unknowns/` without guessed context, description or tags.
- Rework owner context resolution into a hash-bound managed-file plan that atomically
  reconciles the confirmed header and context folder before the same plan is synchronized to
  the database, eliminating DB-only metadata drift.
- Add proven complete-catalog reconciliation with exact inventory matching, soft deactivation
  of missing active rows, empty-scope handling, and truthful
  inserted/updated/unchanged/deactivated accounting. Partial sync never deactivates unrelated
  records.
- Strengthen the `DB_METADATA_CONTEXT` preflight from column-name checks to its exact column
  type, size, precision, scale, nullability, identity, default, check/version and index
  signature, including unexpected objects, disabled or untrusted constraints, index direction,
  triggers and foreign keys.
- Canonicalize SQL Server stored-procedure and function definitions at the declaration
  boundary to `CREATE OR ALTER` without global body replacement.
- Add the corresponding owner CLI, HTTP and MCP surfaces: 34 core MCP tools, 34 HTTP paths and
  4 bridge tools.
- Move new managed output to format version `2`; compatible older validation behavior remains
  explicit.
- Restore exact provider-specific Skill onboarding. Codex uses `$sql-context-pack`, Claude
  Code uses `/sql-context-pack:sql-context-pack`, and Gemini CLI uses `/skills list` plus
  explicit natural-language activation; documentation no longer presents Codex syntax as
  universal.
- Document the 2026-09-14 source and installation security review, including owner-writable
  LocalSystem execution configuration, a reproduced Query Data alias masking bypass, and
  conditional bearer-token transport risks. Evidence and scope are in
  `docs/security-review-2026-09-14.md`.

---

### 2026-07-23 — Query Data (1.2.0)

- Add read-only relational Query Data with dialect-aware parse validation, a function
  allowlist, table resolution against the discovered catalog, literal parameterization,
  deterministic masking, bounded Markdown output and streamed owner CLI output.

---

### 2026-07-20 to 2026-07-21 — Multi-harness install, cross-platform and performance (1.2.0)

- Add `-Harness codex|claude|gemini` to global installation, which previously worked only for
  Codex. Each provider stages its own manifest with the same canonical Skill, installs to that
  provider's own home, and registers through its own CLI verbs. Gemini has no marketplace, so
  its extension directory is the install unit and `--mode skill` is refused with
  `SKILL_MODE_UNSUPPORTED`.
- Improve export performance and add incremental caching with per-object checkpoints, so an
  interrupted export resumes instead of restarting.
- Fix cross-platform issues in the installer and CI, and add uninstall coverage.

---

### 2026-07-19 — Owner installation and profile lifecycle (1.1.0)

- Add a Codex personal-marketplace plugin contract with a mutually exclusive direct global
  Skill fallback, and protected ephemeral MCP configuration for owner-started harness
  sessions.
- Add an interactive host-Python profile wizard that installs safe config files and encrypts
  database host, name, username and password in owner-only runtime storage.
- Add a PATH-independent PowerShell server launcher and a Python module server entry point.
- Add safe `profile list` and protected `harness mcp-list` commands so owners can copy exact
  profile names and verify ephemeral MCP registration.
- Add root `install.ps1` and `sqlctx launch` composition so one explicit owner command can
  configure the first profile, start its loopback child service and open the protected harness
  workflow.
- Add SQL Server DSN-less installed-driver selection (Driver 18 preferred, Driver 17
  fallback), immediate wizard connectivity validation, and safe `profile test` diagnostics.
- Add sanitized per-tool MCP operation events, protected audit storage, server INFO events and
  owner-facing `audit tail` inspection.
- Clarify that SQLFluff is a required runtime dependency while Ruff is optional
  developer/CI-only tooling.
- Remove an unrelated untracked `.agrimap-agent` workflow artifact from the product workspace;
  it was never a SQL Context Pack dependency.

---

### 2026-07-18 — Initial implementation (1.0.3)

- Preserve the authoritative specification and its SHA-256 sidecar, and add
  implementation-routing documents for requirements, architecture, security, output format,
  acceptance, versioning and harness compatibility.
- Add the typed Python package skeleton, public domain models and ports, a central version
  source, a pinned dependency strategy, preflight helpers, CI and three harness manifests.
- Add contract tests for spec integrity, version consistency, public-model safety and path
  validation.
- Add environment-referenced profile loading, protected runtime and credential state,
  encrypted resumable masking keys, deterministic aliases, request-bound approvals, secret
  scanning, host-Python SQLFluff lifecycle and fail-isolated per-file formatting.
- Add reviewed-query adapters for PostgreSQL, MySQL, MariaDB, SQL Server and Oracle, including
  identifier and schema guards, deterministic sampling, capability mapping, cancellation and
  optional lazy drivers.
- Add two-phase full-analysis catalog orchestration with cursor pagination, exact request
  fingerprints, cancellation, retention and quota handling, and dependent-export pinning.
- Add configurable two-pass classification, request-bound persistent owner overrides,
  validated non-authoritative model proposals, classification-change tracking and final
  materialization planning.
- Add relationship and cardinality graph-ready indexes, deterministic SQLFluff-scoped bundles,
  managed-file manifests, assembled-output re-read validation, and a hash-valid realistic
  output fixture.
- Add the complete loopback HTTP surface, strict structured MCP tools and resources, a shared
  typed facade, caller-scoped idempotency, persistent request-bound owner approvals, a
  protected binary fetch CLI, and HTTP/MCP contract examples and parity tests.
- Add the canonical Agent Skill with exact contract and safety references, deterministic
  multi-batch assembly, managed-only repeated updates, nested-output end-to-end coverage,
  bundle traversal rejection and destination-corruption checks.
- Add Codex, Claude Code and Gemini CLI packaging around one canonical Skill, generated
  OpenAPI and MCP schemas, deterministic cross-harness conformance simulation,
  installed-harness smoke validation, and operator and developer documentation.
- Add release wheel and source distribution verification, package-install smoke coverage,
  artifact hashing and a checked-in release report.
- Bind final catalog accounting and sitemap categories to Pass 2 classifications rather than
  preliminary name-based categories.
- Make server validation fail closed on count equations, submitted-inventory hashes, bundle
  hashes and embedded manifest hashes.
- Publish exact typed response schemas for all structured HTTP and MCP operations, and retain
  binary export transfer exclusively on authenticated HTTP and CLI.
