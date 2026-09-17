# Complete export workflow

1. Parse requested output, exact profile/object/category intent and mode.
2. Treat “all/ทั้งหมด” as all permitted TABLE/PROCEDURE/FUNCTION with empty include patterns.
3. If `ETL` may mean an allowed schema, an `ETL_` name prefix, or final category `etl`, inspect the
   safe inventory and ask one consolidated owner question instead of guessing.
4. Get capabilities, safe profiles and session active profile; require connect/change-profile.
5. Check pinned SQLFluff readiness; follow approval when ensure is required.
6. Rediscover only exact retained fingerprints or create an idempotent catalog.
7. Poll preliminary classification and read every category-preview page.
8. In ask/selected mode collect one owner selection; selection never narrows full analysis.
9. Poll full extraction and relationship analysis for every profile-permitted object.
10. Read every analysis sitemap and classification-request page.
11. Submit sanitized proposals only as suggestions. Consolidate owner decisions where required.
12. Read the final materialization plan. In all mode keep unresolved items included under
    `unknowns/`; never invent a fallback category.
13. Confirm final `lut` inclusion and every intentional/security exclusion.
14. Create one server-resolved export with stable idempotency; explicit compatibility batches stay
    at or below 25 objects.
15. Poll beyond 300 seconds while heartbeat/progress changes; report safe phase/count/current object.
16. Treat partial output honestly and read every safe skipped/failed result.
17. Fetch bundles only with `sqlctx export fetch` into OS temp and verify size/bundle/manifest hashes.
18. Assemble with `sqlctx export assemble`; never overwrite unmanaged files.
19. Reread with `sqlctx validate output`, submit complete inventory, output format `2`, and verify
    accounting equations.
20. Clean OS-temp material in `finally` and report exact counts/warnings/unresolved/failures.

21. Sync the index. This step is mandatory and runs on every export, not only when something
    was classified. See "Index sync" below.

## Index sync

Every export ends with one `sqlctx_sync_context_index` call in the default `inventory` mode
covering **every object the catalog discovered**, not only the ones that reached a confirmed
context. Objects whose context could not be determined are submitted as
`classification_status=unresolved` with `classification_source=unknown` and no guessed
context or tags. Submitting an object as unresolved is the correct outcome for an
unclassifiable object; omitting it is not.

Inventory mode needs no approval and cannot overwrite an owner-confirmed classification or
deactivate a row, so there is nothing to ask before running it. If the profile has not
enabled `metadata_context_write` the call returns `METADATA_CONTEXT_WRITE_SCOPE_REQUIRED`
with the exact owner command in `details.owner_command`; report that command and continue
the rest of the export. Do not treat it as an export failure, and do not silently skip the
sync on later runs because it failed once.

One sync call accepts at most 5,000 entries. A catalog larger than that is submitted as
several inventory calls with distinct idempotency keys, never truncated to fit. Inventory
mode is partial-safe — it deactivates nothing — so batching is safe, but every batch must
actually be sent. Sum the per-batch counts before reporting.

Report `inserted`, `updated`, `unchanged`, `owner_values_preserved` and `deactivated`
separately, and state the submitted object count against the discovered object count. If
those two numbers differ, say so and say which objects were left out and why. "The sync
succeeded" is not a complete report without those two numbers.

Then bring the unresolved objects to the owner in one consolidated supervised list. For each
one give the identity, the object type, and a preliminary suggested context with the evidence
behind it, clearly marked as a suggestion. Include every unresolved object; do not sample,
truncate to the interesting ones, or drop objects whose names carry no signal, because those
are exactly the ones the index exists to disambiguate. A wrong context assigned quietly is
worse than an unresolved row, so never promote a suggestion to a confirmed context without
the owner saying so.

Owner answers go back through `owner` mode, which is approval-gated. Owner resolution of an
existing unknown is file-first: create the resolution plan, apply its header/path change, then
sync that same plan into the index.

Use `complete_catalog_id` only when the retained all-mode catalog exactly matches every profile
schema/type, has no include/exclude/profile exclusions, has zero analysis failures, and the
submitted identity inventory exactly matches it. Only that proven mode may deactivate missing
active rows, and it requires `owner` mode.

On interruption, resume only exact request/selection/batch/tooling/source fingerprints. Cancellation is
cooperative; deletion is deliberate owner-approved cleanup.
