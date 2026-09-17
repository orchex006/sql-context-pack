# AGENTS

Rules for any agent or model working in this repository. They outrank the agent's own judgement.
Where a rule here conflicts with a direct instruction from the owner in the current session, ask
once — do not silently pick a side.

The baseline is **2.1.0**, the first released version. Everything before it was internal
development and is history to read, not a contract still in force.

---

## 1. Branches

`main` is protected. Never commit directly to it — not even a typo fix. All work happens on a
branch and is merged back.

| Work | Branch | From | Merges to |
|---|---|---|---|
| Feature, improvement, refactor, docs | `feature/<slug>` | `main` | `main` |
| Urgent fix for something broken | `hotfix/<slug>` | latest release tag | `main` |
| Experiment that may be thrown away | `spike/<slug>` | `main` | never; delete it |

Slug rules:

1. `kebab-case`, lowercase, digits allowed. No spaces, no underscores, no non-ASCII.
2. 3–50 characters, naming the outcome rather than the method —
   `feature/agy-host-support`, not `feature/edit-install-py`.
3. One objective per branch. Never combine unrelated work.
4. If the objective maps to a requirement version, append it:
   `feature/query-guardrails-v1.1`.

Working rules:

1. `git fetch origin` and branch from an up-to-date `origin/main`.
2. Never `git push --force` to `main`. On your own branch use `--force-with-lease`, never bare
   `--force`.
3. A `hotfix/*` has the narrowest scope that actually fixes the problem. No refactors, no
   drive-by cleanup.
4. Merge into `main` with `--no-ff` so each unit of work stays visible.
5. Delete the branch after merging.

---

## 2. Commits

1. Conventional Commits: `<type>(<scope>): <subject>`
   - `type`: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `perf`,
     `security`
   - `scope`: the module — `query-data`, `context-index`, `service`, `doctor`, `skill`
   - `subject`: English, imperative, at most 72 characters, no trailing period
2. Commits are atomic: reverting one leaves the system building.
3. Never commit something that fails `scripts/dev-check.ps1`.
4. Explain **why** in the body whenever the reason is not obvious from the diff.
5. Never `git add -f` an ignored file unless the owner asks in that session.

### Commit identity

Commits are authored as `orchex006`. Configure it per-repository before committing:

```bash
git config user.name orchex006
git config user.email orchex006@users.noreply.github.com
```

Never leave a personal or corporate identity on a commit here, and never attribute one to
another account.

### Never commit these

`.agrimap-agent/`, `.sqlctx-runtime/`, `connection-metadata.json`, `owner-control.json`,
`.env`, `*.sqlctx.zip`, `.tmp-*/`, `.sqlctx-staging/`

Run `git status --short` and actually read it before every commit. Any of these appearing is a
**stop**, not a warning: fix `.gitignore` first.

### Commit what the code needs

Before committing, confirm the change works from a **clean clone**, not just your working tree. A
file that exists locally but was never committed passes locally and fails for everyone else. That
failure mode has already shipped broken code from this repository once: modules imported at
runtime were missing, so `pip install` from the repository could not produce a working package.

---

## 3. Releases and tags

1. Tags are `v<MAJOR>.<MINOR>.<PATCH>`, always with the leading `v`.
2. Tags are annotated (`git tag -a`). Never a lightweight tag.
3. Only tag a commit already on `main`. Never tag a branch.
4. SemVer:
   - **Major** — a contract breaks: an MCP tool removed or changed in meaning, output format
     version changed, managed header shape changed, a CLI command or option removed.
   - **Minor** — a backward-compatible surface added: a tool, an option, a host, an optional
     field.
   - **Patch** — a bug, security or docs fix adding no new surface.
5. Every release has `docs/releases/<version>.md` with migration notes and breaking changes.
6. Confirm every version surface agrees before tagging (§4).

### Version surfaces that must agree

| Surface | Path |
|---|---|
| Package | `src/sqlctx/_version.py` |
| Build | `pyproject.toml` |
| Skill | `skills/sql-context-pack/SKILL.md` |
| Codex | `.codex-plugin/plugin.json` |
| Claude | `.claude-plugin/plugin.json` |
| Claude marketplace | `.claude-plugin/marketplace.json` |
| Gemini | `gemini-extension.json` |
| Antigravity | `.agents/plugin.json` |
| Antigravity marketplace | `.agents/plugins/marketplace.json` |
| Docs | `docs/versioning.md` |

`scripts/validate_manifests.py` must pass before tagging. If it does not yet cover a new surface,
extend the script — never verify by eye. A hardcoded expectation in that script once sat at
`1.3.0` while the product shipped `1.6.0`, which is exactly the drift it exists to catch.

---

## 4. Whenever you touch skills

Any change to `skills/`, `SKILL.md`, its references, the MCP tool surface or a CLI contract
requires all five, in the same change. Never defer one to "next time":

1. Update `metadata.version` in `SKILL.md` and every manifest so they agree.
2. Record the change in `CHANGELOG.md` under the version in progress — what changed and why.
3. Update `docs/versioning.md` and regenerate the affected `docs/generated/` files. Regenerate;
   do not hand-edit.
4. Run `scripts/dev-check.ps1` and `scripts/validate_manifests.py`.
5. Commit with scope `skill`, naming the version in the body.

Never report the work as done until all five are complete.

---

## 5. Updating versions through doctor

```bash
sqlctx doctor update --host <codex|claude|gemini|agy> [--version <x.y.z>] [--source <path>]
```

1. Read the host from the environment or the owner's instruction. **Never infer it from the model
   name.** Claude Code is `claude`, Codex is `codex`, Antigravity is `agy`, legacy Gemini CLI is
   `gemini`.
2. A `--version` must exist as a tag in `orchex006/sql-context-pack`. If it does not, stop and
   report. **Never** fall back to latest silently.
3. No `--version` means the latest release.
4. `doctor`, `doctor status`, `doctor version` and `doctor check` are strictly read-only: no
   install, no cache refresh, no database connection.
5. An `update` the owner typed is already authorised. Do not ask for generic confirmation again.
6. Report success only when package, plugin and service versions all match. Installer exit code 0
   is **not** sufficient.
7. Record a `CHANGELOG.md` entry whenever an update changes the version.

---

## 6. Recording owner prompts

1. Record every prompt the owner submits to `prompts/history/<yyyy-MM-dd>.txt`, dated by
   submission.
2. Store it raw, exactly as typed. Never summarise, correct or translate it.
3. Never record AI responses in that file.
4. Format:

```text
### [<yyyy-MM-dd HH:mm:ss>]
<owner prompt>

### [<yyyy-MM-dd HH:mm:ss>]
<owner prompt>
```

---

## 7. Requirement versions

1. A new requirement from the owner does not immediately create a new version.
2. Being asked to **analyse** a requirement does not create a version either.
3. Once the analysis is presented and the owner asks for it to be **built**, record a new
   requirement version automatically.
4. A new version retains the previous version's content **in full**. This rule is absolute.
5. Only when the owner explicitly changes or removes a rule does the new version drop it.
6. New requirements are **inserted** into the existing content, not written over it.
7. Every requirement version ships a SHA-256 sidecar and canonical LF bytes.
8. Record a `CHANGELOG.md` entry when a requirement version's work is finished. The changelog
   does not replace the requirement history, and the requirement history does not replace it.

The current baseline is [v1.0](docs/spec/design-spec-v1.0.md). Versions after it track product
versions of 2.1.0 or higher. See [Requirements](docs/requirements.md).

---

## 8. Verification

1. Use `scripts/dev-check.ps1` for format, lint, typecheck, test and build wherever possible.
2. Never leave `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `build`, `dist` or
   `*.egg-info` behind, whether the command passed or failed.
3. Caches and build staging belong under the OS temporary directory and are cleaned in a
   `finally`. `.gitignore` is the last line of defence, not the first.
4. Report results honestly. If a test fails, say so and show the output. If you skipped a step,
   say you skipped it. Never report a check as passing without running it.
5. Keep the pytest basetemp path short on Windows; a long one exceeds `MAX_PATH` and produces
   failures that look real but are not.
6. CI runs on Linux; most development here happens on Windows. `dev-check.ps1 -Task typecheck`
   therefore checks `linux`, `darwin` and `win32` targets. Guard platform-specific code with
   `sys.platform == "win32"` rather than `os.name == "nt"` — mypy narrows on the former only —
   and read platform-only attributes with `getattr(obj, "name", default)` rather than `hasattr`.

---

## 9. Knowledge

Read the relevant knowledge base before installing, repairing or diagnosing anything. Do not
guess at steps, and never run install commands speculatively "to see what happens".

| Topic | Read |
|---|---|
| MCP service — install, repair, diagnose, all OSes | [docs/knowledge/mcp-service.md](docs/knowledge/mcp-service.md) |
| SQLFluff — install, version pin, repair | [docs/knowledge/sqlfluff.md](docs/knowledge/sqlfluff.md) |
| Hosts — codex, claude, gemini, agy | [docs/knowledge/hosts.md](docs/knowledge/hosts.md) |
| Common symptoms and fixes | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Service lifecycle | [docs/lifecycle.md](docs/lifecycle.md) |

Repair principles:

1. **Diagnose before changing.** Run `sqlctx doctor --host <host>` and read every entry in
   `findings[]` first.
2. **One layer at a time** — service, then package, then plugin, then skill. Verify between each;
   changing several at once makes it impossible to know what fixed it.
3. **Never delete a profile, credential, registered folder or retained job to fix a problem.** If
   you believe it is necessary, ask the owner first.
4. **Never create a Python environment** or a project-local staging or cache directory.
5. Verify with `sqlctx doctor check --host <host>` afterwards and report all three versions
   honestly.

---

## 10. Safety boundaries

1. Never ask the owner for a database credential, an absolute path or a bearer token.
2. Never echo a token, credential, connection string, raw sample row or large SQL body into an
   MCP payload or a log.
3. `sqlctx_query_data` is read-only SELECT. If you think you need to write to the database, stop
   and tell the owner. Never look for a way around it.
4. Owner approval is single-use and bound to one request. Never auto-grant, never reuse.
5. Never force-push, rewrite history or delete a remote branch without a direct instruction.
6. Report every bound rather than hiding it: truncated results, paging cursors, per-call limits,
   skipped objects and failures, each with its count. Never present a bounded sample as a
   complete set.
