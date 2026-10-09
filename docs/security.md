# Security

The product's guarantee is narrow and worth stating plainly: an agent can read your database
structure and a bounded, protected sample of its data, and can write only to two explicitly enabled
surfaces. It never receives your credentials.

## Credentials and network

- Connection values are encrypted in owner-local runtime storage. Public contracts expose only a
  profile name and its readiness.
- The agent bearer token is separate from the owner approval credential, and neither is a
  database credential.
- The service binds `127.0.0.1` only and creates no firewall rule. Transport is restricted to
  validated IPv4 loopback endpoints, with proxy environment variables and redirects disabled.
- MCP and HTTP accept no arbitrary absolute path. The owner registers a folder; the agent then
  refers to it by an opaque ID.

## Read boundary

- Discovery is always limited by the profile's schema allowlist, object-type allowlist and
  exclusion patterns.
- Query Data accepts relational SELECT only, validated by parsing rather than pattern matching.
  See [Query Data validation](#query-data-validation).
- Table capture is DDL, metadata and a bounded protected, marked sample — never every row.
- A secret scanner redacts SQL literals before export. An object with a residual secret is
  skipped and counted in `skipped_security` rather than exported.

### Query Data validation

A submitted query must parse cleanly as exactly one statement whose top level is a SELECT, a CTE
or a set expression. The following are rejected before execution:

| Rejected | Why |
|---|---|
| INSERT, UPDATE, DELETE, MERGE, TRUNCATE, DROP, ALTER, CREATE | writes and DDL |
| EXEC, EXECUTE, `sp_executesql`, CALL | arbitrary execution |
| OPENROWSET, OPENQUERY, OPENDATASOURCE, BULK, OUTFILE, DUMPFILE | external data access |
| Multiple statements, comments | statement smuggling |
| Cross-database and linked-server references | scope escape |
| `SELECT ... INTO` | writes a new table |
| `FOR XML`, `FOR JSON` | collapse unbounded rows into one cell, escaping row limits |
| `WITH (TABLOCKX \| UPDLOCK \| HOLDLOCK \| NOLOCK \| INDEX(...))`, `OPTION (...)` | locking, isolation and recursion changes reachable with only SELECT permission |
| Functions outside the allowlist, including bare calls like `CURRENT_USER` | information disclosure and side effects |

Tables are resolved against the discovered catalog, so a query cannot reach an object outside
the profile. Literals are replaced with bound parameters. Output is masked per column using
source lineage, so an alias, expression or nested query cannot declassify a sensitive column;
where lineage cannot be resolved, the value is redacted.

On SQL Server the profile must additionally prove it holds no write or admin permission before a
query runs.

## Sensitive data never reaches the model

Every value is protected inside the service before it is returned or exported, so a model —
and anything its provider retains or trains on — never receives real personal or secret data.

- **Classification** uses SQL Server `sys.sensitivity_classifications`, then built-in name
  rules (including Thai/AgriMap abbreviations such as `fname`, `lname`, `tel`, `addr`, `pid`,
  `id_card`, `dob`, `pwd`, `lat`), then value shapes (email, Thai ID, phone, card, titled
  names). Every string is also scanned for embedded personal data.
- **Treatment:** names, Thai IDs, phone, card and account numbers become tool-generated fakes
  in the original layout (fake identifiers start with `0`, so they are never real); emails
  and usernames become aliases; address, birth date and location are generalized; secrets and
  unknown lineage are redacted.
- **Marking:** every protected column is marked in the result header and metadata, e.g.
  `FIRST_NAME ⟨FAKE:PERSONAL_NAME⟩`.
- **Inference guard:** a protected column may only be selected directly; filters, joins,
  grouping, ordering, `CASE` and functions on it are rejected with
  `QUERY_SENSITIVE_USAGE_RESTRICTED`.
- **Reveal handoff:** when real values are needed the model returns the SQL and steps for the
  user to run themselves. `sqlctx query --reveal` prints real values only to an interactive
  terminal after typed confirmation and is not reachable through MCP or HTTP.

Limits are stated in the [3.0.0 release notes](releases/3.0.0.md#known-limits).

## No guessing

A rule or model suggestion never becomes a confirmed context on its own. When evidence is
insufficient the object, file or row stays `UNRESOLVED`, with a null context and description and
an empty tag list. Only the owner supplies a resolution. A deterministic exact-name, prefix or
schema rule may confirm a context only when it yields exactly one answer; ambiguous results and
heuristic suggestions are never promoted.

## Database writes

Both write scopes are disabled by default and are independent of each other.

| Scope | Enables |
|---|---|
| `metadata_context_write` | writing to `[agrimap_app].[DB_METADATA_CONTEXT]` |
| `routine_write` | applying a reviewed procedure or function |

- Enabling `metadata_context_write` is a one-time owner opt-in. After that, `inventory` sync
  runs unattended: it writes machine-derived identity and technical fields only, and can neither
  deactivate a row nor overwrite an `OWNER` classification. Supervised `owner` writes and
  complete-catalog deactivation still require a single-use, request-bound approval.
- Metadata writes require a positive numeric actor ID. It is never derived from a username, OS
  account or harness identity.
- Resolving a context produces an immutable file plan. Applying it in place, syncing the index
  and applying a routine each consume an approval bound to that exact payload.
- Complete deactivation is permitted only when a retained all-mode catalog proves the exact
  unexcluded profile scope, there were zero analysis failures, and the submitted identities match
  the inventory. A partial sync never deactivates anything.
- Index writes verify the table's full DDL signature first and never auto-migrate a drifted
  schema.
- Plans carry an expiry, an identity, an ordered file list and hashes. Content drift stops
  execution before anything runs.
- The routine writer accepts exactly one procedure or function per file and checks header, body
  and path identity. SQL Server uses `CREATE OR ALTER`; engines without a proven safe strategy
  return a stable unsupported error.
- There is no arbitrary SQL, no table deployment, no DROP-and-recreate and no scope widening
  through any write surface.

## Filesystem writes

Writing to a separate output directory is the default. In-place apply requires an explicit plan
and an approval. The scanner rejects symlink and reparse-point escapes and path traversal. An
apply preserves unmanaged files and may only remove or move the exact managed source set named
in its plan.
