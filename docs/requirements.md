# Requirements

Current: [v1.0](spec/design-spec-v1.0.md) · [SHA-256](spec/design-spec-v1.0.sha256)

v1.0 is the baseline requirement for product version 2.1.0, the first released version.

## Why the history resets here

Requirement versions v1.1 through v1.27 were produced during internal development, before
anything was released. They accumulated as stacked revision preambles on top of one base
specification, and their product versions (1.0.3 through 1.6.0) were never published.

At 2.1.0 that chain was collapsed into a single baseline. The dated development record it
described is preserved in [CHANGELOG.md](../CHANGELOG.md), and the full pre-reset history
remains recoverable from the repository history bundle taken before the reset.

## Rules from here

1. v1.0 is the floor. Every later requirement version is **additive** over it and must retain
   the content of the version before it, except where the owner explicitly changes or removes
   a rule.
2. A new requirement version is recorded only when the owner approves the work and asks for it
   to be built — not when they ask for analysis.
3. Every requirement version ships a SHA-256 sidecar and is stored with canonical LF bytes, as
   `.gitattributes` requires. `tests/contract/test_spec_integrity.py` enforces both.
4. Requirement versions from here track product versions of **2.1.0 or higher**.
5. `CHANGELOG.md` records what shipped. It does not replace the requirement history, and the
   requirement history does not replace it.

## Layout

| Path | Contents |
| --- | --- |
| `docs/spec/design-spec-v1.0.md` | The authoritative specification |
| `docs/spec/design-spec-v1.0.sha256` | Its integrity sidecar |
| `prompts/sql_contxt_pack_design_spc_v1.0_start.md` | Byte-identical start prompt |
| `prompts/requiremenr.raw.prompt.md` | Frozen original raw requirement |
| `prompts/history/<yyyy-MM-dd>.txt` | Raw owner prompts, by date |
