# Implementation state

What is actually built, and where the boundaries are. Product `2.1.0`, output format `2`,
Requirement `1.0`, SQLFluff `4.2.2`, MCP SDK `1.28.1`.

## By engine

| Capability | SQL Server | PostgreSQL | MySQL | MariaDB | Oracle |
|---|---|---|---|---|---|
| Discovery, DDL and metadata capture | yes | yes | yes | yes | yes |
| TABLE, PROCEDURE, FUNCTION export | yes | yes | yes | yes | yes |
| Bounded masked sample rows | yes | yes | yes | yes | yes |
| Classification and managed headers | yes | yes | yes | yes | yes |
| Query Data (read-only SELECT) | yes | yes | yes | yes | yes |
| Read-only privilege proof before a query | yes | — | — | — | — |
| `DB_METADATA_CONTEXT` index | yes | — | — | — | — |
| Routine deployment | yes | — | — | — | — |

A dash is a stated boundary, not a crash. The index returns
`METADATA_CONTEXT_ENGINE_UNSUPPORTED` and routine apply returns
`ROUTINE_APPLY_ENGINE_UNSUPPORTED`; both are reported and the rest of the workflow continues.

The read-only privilege proof is SQL Server specific because it uses `HAS_PERMS_BY_NAME` to show
the login holds no INSERT, UPDATE, DELETE, ALTER, CONTROL or EXECUTE right. On the other engines
the guarantee comes from parse-level validation plus a read-only transaction, without a
server-side privilege assertion.

## Features

| Capability | State |
|---|---|
| All-mode materialization, unresolved objects under `unknowns/` | implemented |
| Managed SQL header and output format 2 | implemented |
| SQL Server `CREATE OR ALTER PROCEDURE/FUNCTION` normalization | implemented |
| Comment-tolerant declaration boundary | implemented |
| Per-object analysis failure reporting (`failures[]`) | implemented |
| Registered folder scan, plan, separate apply, approval-gated in-place apply | implemented |
| `[agrimap_app].[DB_METADATA_CONTEXT]` table DDL | implemented; the DBA deploys it |
| Inventory sync of every discovered object, every export | implemented |
| Owner-supervised classification and complete reconciliation | implemented |
| Full schema-signature verification before any index operation | implemented |
| Index-driven generation plan with drift checks | implemented |
| Cross-platform service supervision | implemented |
| Version-pinned updates | implemented |

## Surfaces

| Surface | Size |
|---|---|
| HTTP | 38 operations across 34 paths |
| MCP | 34 core tools, 4 bridge tools, 2 resources |
| Hosts | codex, claude, gemini, agy |

## Not done during verification

No DDL is deployed, no owner database is connected, no installed runtime is modified, and no
release is published, committed or pushed as part of implementation verification.
