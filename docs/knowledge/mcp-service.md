# Knowledge — MCP service

For anyone installing, repairing or diagnosing the SQL Context Pack service. Read it before
acting; do not guess at steps.

## The architecture, first

This is **not** one MCP server per harness session. It is:

```text
codex session ──┐
claude session ─┼─► sqlctx-mcp-bridge   (one per session, stdio, holds no DB state)
agy session ────┘            │
gemini session ──────────────┤
                             │  authenticated HTTP over loopback
                             ▼
              sqlctx service (ONE per machine, persistent)
                       127.0.0.1:8765  /api/v1/*
                             │
                             ▼
                  database adapters + profiles
```

- `src/sqlctx/server/mcp/bridge.py` — the per-session bridge. A thin proxy holding only that
  session's active profile (`SessionProfileRouter`). It opens no database connection.
- `src/sqlctx/server/http/app.py` — the real service. One instance, holding the profile store,
  job store and adapters.

**What follows from this:** if tools are missing or a session misbehaves, repair the **service**
first. Do not restart bridges session by session, and never tell an owner to start a second
server inside a session — it will contend for the port and break the machine for everyone.

## Supervisors

| Host | Supervisor | Artifact |
|---|---|---|
| Windows, service registered | Windows Service (SCM) | via `scripts/windows-service.ps1` |
| Windows, no service | portable pidfile supervisor | `<state>/service.pid`, `<state>/service.log` |
| Linux with systemd | `systemd --user` unit | `~/.config/systemd/user/sql-context-pack.service` |
| macOS with launchctl | launchd user agent | `~/Library/LaunchAgents/com.sql-context-pack.service.plist` |
| Anything else | portable pidfile supervisor | `<state>/service.pid`, `<state>/service.log` |

`<state>` resolves in this order (see `src/sqlctx/service/manager.py`):

1. `$SQLCTX_SERVICE_STATE_DIR` if set
2. Windows: `%LOCALAPPDATA%/sql-context-pack`
3. macOS: `~/Library/Application Support/sql-context-pack`
4. Linux and Unix: `$XDG_STATE_HOME/sql-context-pack`, else `~/.local/state/sql-context-pack`

An owner without Administrator rights still gets one shared service on Windows, through the
portable supervisor.

## Commands

```bash
sqlctx service status
sqlctx service install
sqlctx service start
sqlctx service stop
sqlctx service remove
```

Each returns one JSON object whose `mode` names the supervisor that actually handled it. Read
`mode` and report it accurately — never assume systemd.

`python scripts/service-manager.py <operation>` is the equivalent when working from a checkout.

## Diagnosis order — do not skip steps

### Step 1 — is the service alive?

```bash
sqlctx doctor --host <host>
```

Read every entry in `findings[]`:

| Code | What it actually means | Next |
|---|---|---|
| `SERVICE_NOT_CONFIGURED` | no `connection-metadata.json`; never installed | install the service |
| `SERVICE_UNAVAILABLE` | metadata exists but `/api/v1/health` is unreachable | service is down, or the port is taken |
| `SERVICE_UNHEALTHY` | reachable but health is not `ok` | read the service log |
| `VERSION_DRIFT` | package, plugin and service disagree | update the drifting layer |
| `PLUGIN_VERSION_UNVERIFIED` | the host CLI listing could not be read | see [hosts](hosts.md) |

### Step 2 — isolate the layer

Repair bottom-up, **one layer at a time**, verifying between each. Changing several at once
makes it impossible to tell what fixed it.

```text
service ──► package ──► plugin ──► skill
```

1. **service** — `sqlctx service status`, then `sqlctx service start`
2. **package** — `sqlctx doctor version` against `src/sqlctx/_version.py`
3. **plugin** — `sqlctx doctor check --host <host>`, read `versions.plugin`
4. **skill** — open a new host session; Skill instructions load only at session start

### Step 3 — verify

```bash
sqlctx doctor check --host <host>
```

Report success only when `versions.package`, `versions.plugin` and `versions.service` all match.
An installer exit code of 0 is not sufficient evidence.

## Specific symptoms

### Tools are missing

Expect 34 core MCP tools plus 4 bridge tools (`sqlctx_connect_profile`,
`sqlctx_change_profile`, `sqlctx_disconnect_profile`, `sqlctx_get_active_profile`).

1. Check the service version against the package version. The bridge lists tools *from* the
   service, so an old service hides new tools.
2. Update plugin, package and service together.
3. Open a new session — the tool list is cached at session start.

Do not work around this by starting a second server or bridge in the existing session.

### Port conflict

The service defaults to `127.0.0.1:8765`.

```bash
sqlctx service stop
sqlctx service start
```

In portable mode, a stale pidfile whose process has already exited is detected automatically.
Do not delete the pidfile by hand.

On Windows, if an SCM service is registered, that service owns the lifecycle. Starting a portable
copy beside it would give you two servers fighting over the same port, so `sqlctx service` refuses
and tells you to use the Windows installer instead.

### Bearer token and connection metadata

`connection-metadata.json` lives in the runtime directory. Never echo its contents, never put it
in a log, and never show it to the owner. If you suspect it is damaged, reinstall the service,
which generates a fresh token.

Transport is bound to IPv4 loopback, with proxies and redirects disabled. If you hit a connection
error, do not remove those guards to make it pass.

## Rules

1. Never create a Python environment or a project-local staging or cache directory.
2. Never delete a profile, credential, registered folder or retained job to "clean up". If you
   believe it is necessary, ask the owner first.
3. Never start the service by hand with `python -m sqlctx.server.http.app` inside a harness
   session. Use `sqlctx service` so the supervisor owns the lifecycle.
4. Never change the port without telling the owner. The bridge reads the port from the metadata;
   a mismatch disconnects everything.
