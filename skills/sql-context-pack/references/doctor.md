# Doctor and upgrades

Resolve the selected host from the current environment/user request: codex, claude, gemini or agy.
Never infer host from the model name. Claude Code is `claude`, Codex is `codex`, Antigravity is
`agy`, and legacy Gemini CLI is `gemini`. The CLI defaults to codex; pass --host for another host.

`agy` (Antigravity) is Google's successor to Gemini CLI. It has no plugin or extension CLI, so it
installs by file placement under `~/.gemini/config/skills/` plus a merge into
`~/.gemini/config/mcp_config.json`, and its reported version comes from the installed SKILL.md.
See [docs/knowledge/hosts.md](../../../docs/knowledge/hosts.md).

| Request | Command | Effect |
| --- | --- | --- |
| doctor / status | `sqlctx doctor --host codex` | Read installed/source/plugin/service versions, Python/SQLFluff and safe profile readiness |
| version | `sqlctx doctor version --host codex` | Print package version only; no network probe |
| check | `sqlctx doctor check --host codex` | Read local readiness and report drift |
| check online | `sqlctx doctor check --online --host codex` | Also read latest GitHub release metadata; no Git/cache refresh |
| MCP check | `sqlctx doctor --mcp --host codex` | Preserve existing authenticated tool-list readiness probe |
| update | `sqlctx doctor update --host codex` | Explicitly update selected host package and shared local runtime |
| update to a version | `sqlctx doctor update --host codex --version 2.1.0` | Pin the update to one released tag |
| service state | `sqlctx service status` | Report the one shared service and which supervisor owns it |

Default/status/version/check are read-only: do not install, refresh caches, change profiles, open
database connections or start a lifecycle just to answer them. Report errors and version drift;
never include connection metadata contents, tokens or credentials in the response.

An explicit update authorizes the update action. Run the exact owner CLI command; do not ask for
another generic confirmation. Windows may request elevation for protected installation files and
service registration. Preserve profiles, credentials, registered folders and retained work.

`--version <x.y.z>` pins the update to exactly that released tag. A version that does not exist is
a stop that reports the available tags; never fall back to latest silently, and never round a
request for one version into another. Omitting `--version` installs the latest release.

Update runs on every platform: Windows uses `install.ps1` and the SCM service, and other hosts use
`install.sh` with the systemd/launchd/pidfile supervisor selected by `sqlctx service`.

Update accepts `--source <trusted-checkout>` when install provenance is missing. It requires a clean
main checkout tracking origin/main of orchex006/sql-context-pack. A dirty/foreign source is a stop;
never delete changes, change remotes or reset branches automatically. `sqlctx repair --source ...`
is for an explicitly reviewed local build and deliberately performs no Git download.

Report success only when installed package, selected plugin and running service versions match.
A successful installer exit alone is insufficient. Reopen the host session to load changed Skill
instructions. Package upgrade does not authorize publishing a GitHub release or changing unrelated
host software. The shared service upgrade also benefits other installed hosts; do not reinstall
their plugins unless selected.
