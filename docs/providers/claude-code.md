# Claude Code

Claude Code separates adding a marketplace from installing a plugin, per the
[Anthropic plugin documentation](https://code.claude.com/docs/en/discover-plugins).

## Install

```powershell
claude plugin marketplace add orchex006/sql-context-pack
claude plugin install sql-context-pack@sql-context-pack
```

Start a new session, then invoke the Skill using Claude Code's namespace:

```text
/sql-context-pack:sql-context-pack setup
```

Start another new session once the service is ready, then:

```text
/sql-context-pack:sql-context-pack profiles
/sql-context-pack:sql-context-pack connect <profile-name>
```

Do not use the Codex `$sql-context-pack` syntax here.

If the plugin was installed mid-session, `/reload-plugins` can pick it up — but when the MCP tool
count changes, verify in a fresh session.

## Upgrade

```powershell
claude plugin marketplace update sql-context-pack
claude plugin install sql-context-pack@sql-context-pack
```

That updates Claude's cache only. Complete the lifecycle so the owner package and service move
too:

```bash
sqlctx doctor update --host claude
```

## Verify

```bash
sqlctx doctor check --host claude
```

Report success only when package, plugin and service versions all match.

## Uninstall

```powershell
.\scripts\lifecycle.ps1 -Operation uninstall -Harness claude
```

The direct command is `claude plugin uninstall sql-context-pack@sql-context-pack`, but always
remove the managed runtime first.
