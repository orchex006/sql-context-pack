# Gemini CLI

> **Deprecated for most users.** Google stopped serving Gemini CLI for consumer accounts
> (AI Pro, Ultra and free tier) in June 2026. Access continues for Gemini Code Assist Standard
> and Enterprise licences and for paid API keys.
>
> The successor is [Antigravity (`agy`)](antigravity.md). This host remains supported here and is
> unchanged; migrate when it suits you, not because this page forces it.

Gemini CLI installs an extension from a Git repository and manages it with `gemini extensions`,
per the [extension reference](https://geminicli.com/docs/extensions/reference/).

## Install

```powershell
gemini extensions install https://github.com/orchex006/sql-context-pack
```

Quit Gemini CLI and reopen it, then confirm the Skill was discovered:

```text
/skills list
```

Gemini loads **extensions**, not bare skills: a directory without `gemini-extension.json` is
never discovered, which is why `--mode skill` is refused with `SKILL_MODE_UNSUPPORTED`.

## Use it

There is no repository-specific slash command. Ask in natural language, naming the Skill:

```text
Use the sql-context-pack skill to run setup.
Use the sql-context-pack skill to list profiles.
Use the sql-context-pack skill to connect profile <profile-name>.
```

Open a new session once the service is ready. Extension changes take effect after a restart. Do
not use the Codex `$sql-context-pack` syntax or the Claude Code namespace here.

## Upgrade

```powershell
gemini extensions update sql-context-pack
```

Then complete the lifecycle so the owner package and service move too:

```bash
sqlctx doctor update --host gemini
```

## Uninstall

```powershell
.\scripts\lifecycle.ps1 -Operation uninstall -Harness gemini
```

The direct command is `gemini extensions uninstall sql-context-pack`, but use the lifecycle
script first so the service and owner package are not left behind.
