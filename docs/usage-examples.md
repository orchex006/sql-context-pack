# Usage examples

Three worked flows, from simple to advanced. All of them use a profile the owner already
created, and none of them pass credentials or a connection string through a prompt.

## Example 1 — Simple: export everything

Goal: export every object the profile allows, without sorting categories by hand.

Prompt:

```text
Use SQL Context Pack. Connect profile agrimap-dev, then build all context with
selection.mode=all covering TABLE, PROCEDURE and FUNCTION. Keep anything you cannot
classify in unknowns rather than guessing. Then give me the fetch, assemble and
validate commands to run on my side.
```

Check the result:

- Discovered, analyzed, failed, materialized and unresolved counts are reported **separately**
  and reconcile with each other.
- Every SQL file carries a managed header.
- Table samples are masked and bounded.
- SQL Server procedures use `CREATE OR ALTER PROCEDURE`.
- The submitted count matches the discovered count. If it does not, the agent should say which
  objects were left out and why.

## Example 2 — Intermediate: classify a folder and record decisions

Goal: organise existing SQL into context folders, then record the contexts you confirm.

```bash
sqlctx folder register --input-root D:\sql\incoming --output-root D:\sql\classified --engine sqlserver
sqlctx folder plan   --folder-id <folder-id>
sqlctx folder apply  --plan-id <initial-folder-plan-id>

sqlctx context-index resolve --folder-id <folder-id> \
  --file unknowns/tables/dbo_APP_STATE.sql \
  --context app_state --description "Application state" --tag app_state --tag share
sqlctx folder apply --plan-id <resolution-plan-id>

sqlctx profile write-scope --profile agrimap-dev --metadata-context-write
sqlctx context-index sync-plan --profile agrimap-dev --plan-id <resolution-plan-id> \
  --actor-id 123 --idempotency-key sync-app-state-01
sqlctx approvals grant
```

`resolve` builds a plan and writes nothing to the index. After granting an approval for a
privileged apply or owner-mode sync, retry with the **same** plan, payload and idempotency key.

Files you did not give a context to must stay in `unknowns/`. Do not let description or tags be
invented from a filename.

Note the division of labour: recording *identity* for every discovered object happens
automatically on each export. This flow is for recording the *decisions* only you can make.

## Example 3 — Advanced: select inputs by context, then update routines

Goal: choose generation inputs by context and tag, then safely update several routines.

```bash
sqlctx context-index generate-plan --profile agrimap-dev --folder-id <folder-id> \
  --context content --tag share --object-type procedure

sqlctx profile write-scope --profile agrimap-dev --metadata-context-write --routine-write
sqlctx routine plan  --profile agrimap-dev --folder-id <folder-id> --idempotency-key deploy-content-01
sqlctx routine apply --plan-id <routine-plan-id>
sqlctx approvals grant
```

Retry the apply with the same plan ID after granting.

A generation plan stops when the database index, the managed header or the SQL body hash
disagree — treat that as a stop, not a warning.

A routine plan accepts exactly one procedure or function per file, rechecks identity and hashes
immediately before executing, reports per-file results, and never falls back to DROP-and-recreate
when an engine or definition is unsupported.

## Reading the answer critically

Whatever the flow, these are the questions worth asking the agent:

- How many objects were discovered, and how many reached the output?
- Was any result truncated, and by which bound?
- Which objects were skipped for security reasons?
- Which objects are still unresolved, and what does it suggest for each?

A bounded sample presented as a complete set is the failure mode to watch for.
