# Codex

Codex installs from this repository's plugin marketplace. The commands below follow the current
[Codex plugin CLI reference](https://developers.openai.com/codex/cli/reference).

## Install

```powershell
codex plugin marketplace add orchex006/sql-context-pack
codex plugin add sql-context-pack@sql-context-pack
```

Open a new Codex room and run:

```text
$sql-context-pack setup
```

On Windows, approve the UAC prompt only if you want the SCM-registered service; without
Administrator the portable supervisor is used instead.

Open a new room again once setup finishes, then:

```text
$sql-context-pack profiles
$sql-context-pack connect <profile-name>
```

Skill instructions load at room start, which is why the new room matters.

## Upgrade

```powershell
codex plugin marketplace upgrade sql-context-pack
codex plugin list --json
```

Refreshing the marketplace updates the plugin only. Complete the lifecycle so the owner package
and service move too:

```bash
sqlctx doctor update --host codex
```

Then open a new room. Never start a second server with `sqlctx launch` in an existing room.

## Verify

```bash
sqlctx doctor check --host codex
```

`doctor` reads the version from `codex plugin list --json`, matching an entry whose `name` is
`sql-context-pack` or whose `pluginId` starts with `sql-context-pack@`, and which is enabled.

A `PLUGIN_VERSION_UNVERIFIED` finding means that listing could not be read — often a cache-only
install with no local staging metadata. Check `codex plugin list` directly before concluding the
plugin is broken.

## Uninstall

Remove the managed runtime before the plugin, so the lifecycle script still exists:

```powershell
.\scripts\lifecycle.ps1 -Operation uninstall -Harness codex
```

That removes the service, owner package, plugin and the `sql-context-pack` marketplace entry,
while deliberately keeping encrypted profiles and retained runtime data.
