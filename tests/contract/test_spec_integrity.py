"""Integrity checks for the single authoritative requirement specification.

The requirement history was reset to one baseline (v1.0) at product 2.1.0, so these checks
cover that baseline rather than a chain of superseded versions. When a new requirement
version is added it must be additive over v1.0 and carry its own SHA-256 sidecar; extend
`_versions` rather than replacing the baseline in place.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = ROOT / "docs/spec"
BASELINE = "v1.0"


def _versions() -> list[str]:
    found = sorted(
        path.stem.removeprefix("design-spec-") for path in SPEC_DIR.glob("design-spec-v*.md")
    )
    assert BASELINE in found, f"the {BASELINE} baseline specification is missing"
    return found


def test_every_specification_matches_its_sidecar() -> None:
    for version in _versions():
        spec = SPEC_DIR / f"design-spec-{version}.md"
        sidecar = SPEC_DIR / f"design-spec-{version}.sha256"
        expected = sidecar.read_text(encoding="utf-8").split()[0].lower()
        assert hashlib.sha256(spec.read_bytes()).hexdigest() == expected, version


def test_specifications_are_stored_with_canonical_lf_bytes() -> None:
    """.gitattributes declares docs/spec/*.md as eol=lf, so a CRLF copy would break its hash."""
    for version in _versions():
        raw = (SPEC_DIR / f"design-spec-{version}.md").read_bytes()
        assert b"\r\n" not in raw, f"design-spec-{version}.md contains CRLF line endings"


def test_start_prompt_and_preserved_specification_are_identical() -> None:
    preserved = SPEC_DIR / f"design-spec-{BASELINE}.md"
    prompt = ROOT / f"prompts/sql_contxt_pack_design_spc_{BASELINE}_start.md"
    assert prompt.read_bytes() == preserved.read_bytes()


def test_only_the_reset_baseline_remains() -> None:
    """The v1.1-v1.27 development chain was removed; nothing may quietly reintroduce it."""
    assert _versions() == [BASELINE]
    assert not list((ROOT / "prompts").glob("sql_contxt_pack_design_spc_v1.[1-9]*_start.md"))
    assert not (ROOT / "prompts/versions").exists()


def test_baseline_declares_the_current_product_version() -> None:
    from sqlctx import __version__

    text = (SPEC_DIR / f"design-spec-{BASELINE}.md").read_text(encoding="utf-8")
    assert f"Product/package/Skill version: {__version__}." in text
    assert re.search(r"^\*\*Specification version:\*\* `1\.0`$", text, re.MULTILINE)


def test_frozen_raw_requirement_hash() -> None:
    raw = ROOT / "prompts/requiremenr.raw.prompt.md"
    expected = (ROOT / "prompts/requiremenr.raw.prompt.sha256").read_text().split()[0].lower()
    normalized = raw.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(normalized).hexdigest() == expected
