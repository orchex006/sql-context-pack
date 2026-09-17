# Knowledge — SQLFluff

SQLFluff has two jobs here, and the second matters more than most people expect:

1. **Formatter** — gives exported SQL one consistent shape.
2. **Guardrail parser** — `sqlctx_query_data` uses `sqlfluff.core.Linter` to parse a query and
   prove it is a read-only SELECT (`src/sqlctx/query_data/validation.py`).

Because of the second job, **a wrong SQLFluff version changes guardrail behaviour**, not just
formatting. That is why the version is pinned hard, and why an agent must never change the pin to
make a check pass.

## The pinned version

`src/sqlctx/_version.py` → `SQLFLUFF_VERSION` (currently `4.2.2`).

That constant is the single source of truth. Changing it is an owner decision, and it must be
accompanied by updates to `docs/versioning.md` and `CHANGELOG.md` in the same change.

## Commands

| Command | Does | Approval |
|---|---|---|
| `sqlctx sqlfluff status` | Read-only; reports the installed version and `ready` | no |
| `sqlctx sqlfluff ensure` | Installs the pinned version if it does not match | yes |
| `sqlctx sqlfluff update --version <x.y.z>` | Moves to a different exact version | yes |

MCP equivalents: `sqlctx_sqlfluff_status`, `sqlctx_sqlfluff_ensure`, `sqlctx_sqlfluff_update`.

## Reading status correctly

`status()` returns a `HostPythonToolingDescriptor`:

| Field | Meaning |
|---|---|
| `sqlfluff_version` | the version actually installed; `null` means absent |
| `ready` | true only when the installed version matches the pin **exactly** |
| `python_version` | must be >= 3.11.0, otherwise `PYTHON_UNAVAILABLE` |
| `environment_owner` | `host` — the system may install; `owner` — verify only |
| `update_blocked_by_active_jobs` | an export or format job is running |

`ready: false` does not mean "SQLFluff is missing". It means "the version does not match" — and
that includes a **newer** version, which is also not ready, because parser behaviour may differ.

## Repair

### `ready: false` with `environment_owner: host`

```bash
sqlctx sqlfluff ensure
```

This runs `<host-python> -m pip install --user sqlfluff==<pinned>` under a cross-process lock,
then verifies. If verification fails it raises `ToolingUnavailable` without writing state.

### `environment_owner: owner`

This environment is verify- and execute-only. The system refuses to install and raises
`OWNER_MANAGED_PYTHON_ENVIRONMENT`.

**Stop** and ask the owner to install it themselves, using the same Python the service uses:

```bash
<host-python> -m pip install --user sqlfluff==<pinned-version>
```

Never find a different Python to install into, and never create a virtual environment.

### `update_blocked_by_active_jobs: true`

`update` raises `TOOLING_BUSY` (409, retryable). Wait for the export or format job to finish.
Never cancel the owner's job to let an update through.

### An update that fails

`update()` rolls back on its own: if the new version fails its self-test, it reinstalls the
previous version and raises `ToolingUnavailable` saying so. Read the whole error before running
pip yourself.

## Error codes

| Code | Cause | Action |
|---|---|---|
| `PYTHON_UNAVAILABLE` | host Python will not run, or is < 3.11 | environment problem; the owner fixes it |
| `OWNER_MANAGED_PYTHON_ENVIRONMENT` | verify-only environment | the owner installs it |
| `INVALID_SQLFLUFF_VERSION` | version string is not digits and dots | use an exact stable version, e.g. `4.2.2` |
| `TOOLING_BUSY` | a job is running | wait, then retry |

## Rules

1. **Never edit `SQLFLUFF_VERSION`** to make `ready` true. That is closing your eyes, not fixing
   anything.
2. Never run `pip install sqlfluff` without pinning the version.
3. Never install into a different Python than the service uses. The `executable_fingerprint`
   would stop matching and formatted exports would be invalid.
4. Never use `--break-system-packages` or `sudo pip` to force it through.
5. If an export stops because SQLFluff is not ready, **do not** disable formatting to keep it
   moving. Formatting is part of the managed output contract, not decoration.
