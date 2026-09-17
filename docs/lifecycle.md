# Install, upgrade, repair and uninstall

Four layers must be at the same revision: the host plugin or extension, the owner Python
package, the shared loopback service, and the MCP bridge. Refreshing only the plugin cache is
not a complete lifecycle operation, and is the most common cause of "the tools are missing".

## Prerequisites

- Windows, Linux, macOS or another Unix host with Python `3.11+` on `PATH`.
- Windows: PowerShell. Administrator is requested only for Windows Service registration and the
  ProgramData ACL — and only if you want the SCM service. Without Administrator the portable
  supervisor is used instead.
- Linux: `systemd --user` when available, otherwise a supervised background process.
- macOS: a user LaunchAgent when available, otherwise a supervised background process.
- Git, to install from the repository.
- A driver for your database. SQL Server uses ODBC Driver 18, falling back to 17.

No virtual environment is needed or wanted. The owner package installs with `pip --user`,
Linux/macOS/Unix never use root or `sudo`, and every platform stages under the OS temporary
directory.

## Install

Pick a host first: [Codex](providers/codex.md), [Claude Code](providers/claude-code.md),
[Antigravity](providers/antigravity.md) or [Gemini CLI](providers/gemini-cli.md). Then run the
Skill setup, which uses the installer from the plugin cache.

| Host | Invocation |
|---|---|
| Codex | `$sql-context-pack setup` |
| Claude Code | `/sql-context-pack:sql-context-pack setup` |
| Antigravity | ask in natural language; confirm the server with `/mcp` |
| Gemini CLI | `/skills list`, then "Use the sql-context-pack skill to run setup." |

Antigravity and Gemini CLI have no repository-specific slash command.

From a source checkout you have reviewed:

```powershell
.\install.ps1
```

```bash
./install.sh
```

The installer runs a Python preflight, builds a wheel when the fingerprint changed, installs the
package and bridge, opens the secure profile wizard if no profile exists, installs the service
bound to `127.0.0.1`, and health-checks before reporting success.

`install.sh` accepts `--harness <codex|claude|gemini|agy>`, `--update`, `--repair`, `--mode`,
`--skip-service` and `--port`.

## Upgrade

The supported path is one command:

```bash
sqlctx doctor update --host <codex|claude|gemini|agy>
sqlctx doctor update --host agy --version 2.1.0   # pin an exact release
```

A version that does not exist as a tag is a stop that lists the available tags. It never falls
back to the latest release.

Update works on every platform: Windows drives `install.ps1` and the SCM service, other hosts
drive `install.sh` and the supervisor that `sqlctx service` selected.

Always open a new session afterwards — Skill instructions load at session start.

If an older profile lacks `FUNCTION`, widen it yourself with `sqlctx profile scope`. Scope is
never widened automatically.

## Repair

Diagnose before changing anything:

```bash
sqlctx doctor --host <host>
sqlctx service status
```

Then repair one layer at a time — service, package, plugin, skill — verifying between each.
Changing several at once makes it impossible to tell what actually fixed it.

```powershell
.\install.ps1 -Repair
.\install.ps1 -Repair -RepairComponent mcp
.\install.ps1 -Repair -RepairComponent package
.\install.ps1 -Repair -RepairComponent service
```

```bash
./install.sh --repair
sqlctx service status
sqlctx service start
```

Full procedures are in [the service knowledge base](knowledge/mcp-service.md). Never delete a
profile, credential, registered folder or retained job to "clean up" a problem.

## Uninstall

Run this from the installed plugin or extension root, before deleting the bundle.

```powershell
.\scripts\lifecycle.ps1 -Operation uninstall -Harness codex
.\scripts\lifecycle.ps1 -Operation uninstall -Harness claude
```

```bash
sqlctx service remove
python3 -m pip uninstall sql-context-pack
```

Then remove the host registration:

```bash
codex plugin remove sql-context-pack@sql-context-pack
claude plugin uninstall sql-context-pack@sql-context-pack
gemini extensions uninstall sql-context-pack
```

For Antigravity, delete `~/.gemini/config/skills/sql-context-pack/` and remove the
`sql-context-pack` entry from `~/.gemini/config/mcp_config.json`. Leave the other entries alone.

Add `-KeepNativePlugin` on Windows to keep the native plugin temporarily.

Encrypted profiles and retained runtime data are deliberately preserved. Deleting them is a
separate owner decision, not part of uninstall.

## Readiness checklist

- `sqlctx doctor` passes, and package, plugin and service versions all match.
- `sqlctx --help` lists this revision's commands: `folder`, `context-index`, `routine` and
  `service`.
- The selected profile reports `ready=true` and its connection test passes.
- The generated contract reports 34 core MCP tools, and the bridge adds 4.
- A new session sees the same Skill and tool set.
- `metadata_context_write` and `routine_write` are still `false` unless you enabled them.

## Version 2.1.0

See the [release and migration notes](releases/2.1.0.md) before upgrading: Query Data rejects
constructs that 1.6.0 accepted, and `sqlctx_sync_context_index` gained a `mode`. Full history is
in [CHANGELOG.md](../CHANGELOG.md).
