# Security review — 2026-09-14

A review of source code, installer, hooks, HTTP and MCP authentication, credential storage, Query
Data masking, and Windows Service and ACL state, carried out before the implementation was
shipped and the service was configured.

**Status: historical.** Every finding below was resolved in 1.5.0 or 2.1.0. It is retained as the
record of what was found and how it was answered, not as a list of open issues. Resolution status
is stated per finding.

## Summary

No evidence of a backdoor, and no evidence of a token being sent off the machine. The genuine
problems were concentrated in two areas: privilege escalation through the Windows Service, and
secret leakage in Query Data output.

At review time the `SQLContextPack` service was Running as `LocalSystem`, and the listener on
port 8765 was bound to `127.0.0.1` only — not to every network interface. That binding was
confirmed, not assumed.

---

## 1. High — LocalSystem executing a Python interpreter under a user-writable path

**Resolved in 1.5.0.**

- `scripts/windows-service.ps1` granted the owner `FullControl` over the managed root.
- The same script registered the service as `LocalSystem`.
- `scripts/sqlctx_windows_service.py` read `python_executable` from `service-config.json` and
  launched it as a subprocess.
- In practice the service used a Python interpreter installed under the owner's own profile
  directory, and both that executable and `C:\ProgramData\SQLContextPack\service-config.json`
  granted the owner account `FullControl`.

**Impact.** Anything running as the owner account could replace the interpreter or its
configuration and obtain execution at `SYSTEM` privilege the next time the service restarted or
the machine rebooted. This is a local privilege-escalation path, not a remote takeover: it
requires code already running as that user.

**Resolution.** 1.5.0 replaced `LocalSystem` with a dedicated virtual service account, installed
a protected copy of CPython, and applied exact ACLs to the application and its configuration so
that the executable, interpreter, libraries and service configuration can only be modified with
elevation. Runtime data the owner must write was separated from the executable and configuration
set.

Reference: [LocalSystem account](https://learn.microsoft.com/en-us/windows/win32/services/localsystem-account)

---

## 2. High — A SQL alias could carry a token or password past masking

**Resolved in 1.5.0.**

- `src/sqlctx/query_data/service.py` passed the *output* column name to the masker rather than
  resolving the source column lineage.
- `src/sqlctx/security/masking.py` classified from that name and returned `PUBLIC` when it did
  not match a sensitive pattern.
- `src/sqlctx/adapters/base.py` takes the column name from the DB-API cursor description, which
  reflects the alias, not the source.

Confirmed by running `QueryDataService` and `QueryValidator` against a fake database adapter
holding a synthetic secret:

| Query | Accepted by validator | Secret visible in Markdown |
|---|---|---|
| `SELECT access_token FROM dbo.CONTENT_SHARE` | yes | no |
| `SELECT access_token AS public_value FROM dbo.CONTENT_SHARE` | yes | **yes** |

**Impact.** A caller with only query permission could retrieve an opaque secret simply by
renaming the column.

**Resolution.** 1.5.0 introduced source lineage (`src/sqlctx/query_data/lineage.py`). Masking now
follows the *source* column through aliases, expressions, nested queries, CTEs, unions, wildcard
renaming and JSON extraction. Where lineage cannot be resolved the value is redacted rather than
emitted. `tests/unit/test_query_lineage_security.py` asserts that a secret cannot be
declassified by any of those routes.

Note that the fix shipped in the repository only at 2.1.0: the 1.5.0 implementation existed in
the author's working tree but was never committed, so a clean clone still contained this bypass
until 2.1.0. See [CHANGELOG.md](../CHANGELOG.md).

---

## 3. Related hardening delivered alongside

**Bearer transport.** 1.5.0 restricted bearer transport to validated IPv4 loopback endpoints and
disabled proxy environment variables and redirects, so a hostile `HTTP_PROXY` or a redirect could
not move an authenticated request off the loopback interface.

**Update provenance.** 1.5.0 validated the update remote, branch and worktree before installing,
so an update could not be sourced from an untrusted remote or a dirty checkout. 2.1.0 added
version pinning, where an unknown version stops rather than silently installing the latest
release.

**Query Data read path.** 2.1.0 closed three further read-path bypasses found by systematically
probing the validator: row-collapsing `FOR XML` and `FOR JSON`, table and query hints reachable
with only SELECT permission, and parenthesis-free function calls that skipped the allowlist. See
[Security](security.md#query-data-validation).

---

## Scope and limits of this review

This review read source, installer scripts, hooks and local service state. It did not:

- connect to or test against a production database;
- assess the security of the owner's database server, network or Active Directory;
- audit GitHub organisation settings beyond visibility, branch protection and CI trust
  boundaries;
- perform dynamic analysis or fuzzing beyond the targeted Query Data reproduction above.

GitHub settings were inspected, not changed. A checked-in ruleset file is not proof that remote
protection is enabled — that must be verified in the repository settings.

## Threat model assumed

An external attacker without credentials, plus a local attacker who already has code execution
as the owner account. Findings 1 and 2 are meaningful under the second assumption; neither
provided remote entry on its own.
