# Operation contract

The service exposes 34 core `sqlctx_*` tools, four session bridge tools, and exactly two small export
resources. Generated schemas are authoritative. ZIPs, credentials, unrestricted paths and large SQL
bodies are never MCP resources.

## Catalog/export

- All mode requires empty include patterns; otherwise `ALL_MODE_INCLUDE_FILTER_CONFLICT`.
- All successfully extracted plan items are materialized. Unresolved items go to `unknowns/` and do
  not raise `ALL_MODE_UNRESOLVED_OBJECTS`.
- Omitted export `object_ids` means server-resolved full plan; explicit batches remain at most 25.
- Stable idempotency key + same caller/request returns retained work; changed payload returns
  `IDEMPOTENCY_CONFLICT`.
- Read every preview/sitemap/classification/job cursor until `next_cursor=null`.
- Query Data is relational SELECT only, masked, 1–500 rows over MCP; no MCP `all_rows`.

## Managed folders

- `sqlctx_list_managed_folders` returns opaque IDs and safe engine flags, never roots.
- `sqlctx_plan_folder_classification` is non-mutating and includes every scanned file, destination,
  identity, status, warnings and hashes.
- `sqlctx_apply_folder_classification` verifies unchanged hashes; in-place plans require approval.
- Reject links/reparse escapes, traversal, multi-object files, duplicate identities and collisions.

## Context index and generation

- `sqlctx_list_context_index` is read-only/paginated and filters context, tag, type, status, active.
- `sqlctx_resolve_context_index` is non-mutating and returns an immutable in-place managed-folder
  plan for one current file. Apply that plan before calling `sqlctx_sync_context_index` with its
  entries; a DB-only resolution is forbidden.
- `sqlctx_sync_context_index` requires `metadata_context_write`, positive actor ID, idempotency and
  owner approval. Without `complete_catalog_id` it is partial and never deactivates other rows.
- Complete sync may soft-deactivate missing rows only after an exact unfiltered all-mode catalog with
  zero analysis failures proves the full profile scope and submitted identities match its inventory.
  The result reports inserted, updated, unchanged, deactivated and owner-values-preserved counts.
- Every index operation fails closed on table signature drift across columns/types/nullability/
  identity/defaults/checks/indexes, unexpected constraints/indexes/triggers/foreign keys, and index
  key direction; the runtime never auto-migrates the table.
- Owner-confirmed context/description/tags are not overwritten by rule/unknown sync.
- `sqlctx_plan_context_generation` excludes unresolved unless explicitly included and returns only
  metadata/relative paths. Index/header/body mismatch is `METADATA_CONTEXT_GENERATION_DRIFT`.

## Routine deployment

- `sqlctx_plan_routine_deployment` binds profile, caller, engine, ordered files, hashes and expiry.
- `sqlctx_apply_routine_deployment` requires `routine_write`, exact plan binding and owner approval.
- SQL Server Procedure/Function use reviewed CREATE OR ALTER normalization.
- Unsupported engines return `ROUTINE_APPLY_ENGINE_UNSUPPORTED`; never DROP/recreate.

## Approval

Privileged calls first return `APPROVAL_REQUIRED` with challenge ID, expiry and exact
`sqlctx approvals grant --challenge ID`. The owner grants locally; retry the identical operation.
Never ask for the owner credential or change the payload after grant.

## Completion

```text
discovered = fully_analyzed + analysis_failed
fully_analyzed = materialized + intentionally_excluded
format_requested = formatted + parse_failed_preserved + format_failed_preserved
format_requested = materialized
```

Stop on unsafe paths, credential-policy failure, weakened masking, hash/schema drift, invalid header,
unproven inventory or failed validation.
