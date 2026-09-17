"""Conservative source-name lineage for result masking, independent of output aliases.

Simple projections retain their source names. Complex scopes share a conservative
dependency set; wildcard renaming and JSON extraction have unknown lineage and
are redacted. This is an output confidentiality boundary, not SQL authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _identifier(raw: str) -> str:
    if raw.startswith("[") and raw.endswith("]"):
        return raw[1:-1].replace("]]", "]")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "`"}:
        return raw[1:-1].replace(raw[0] * 2, raw[0])
    return raw


def _sources(segment: Any) -> tuple[str, ...]:
    names = []
    for reference in segment.recursive_crawl("column_reference"):
        identifiers = [item.raw for item in reference.raw_segments if item.is_type("identifier")]
        if identifiers:
            names.append(_identifier(identifiers[-1]))
    return tuple(sorted(set(names)))


@dataclass(frozen=True)
class MaskingLineage:
    # None means unknown and must never be treated as public.
    projections: tuple[tuple[str, ...] | None, ...] = ()
    fallback: tuple[str, ...] | None = ()
    complex_scope: bool = False

    def sources(self, index: int, column_count: int) -> tuple[str, ...] | None:
        if self.projections:
            if len(self.projections) != column_count:
                return None
            return self.projections[index]
        return self.fallback


def _unknown_expression(segment: Any) -> bool:
    # JSON functions can remove a secret's key before the recursive JSON masker sees it.
    for name in segment.recursive_crawl("function_name"):
        if "json" in name.raw.casefold():
            return True
    return False


def build_lineage(tree: Any) -> MaskingLineage:
    clauses = list(tree.recursive_crawl("select_clause"))
    wildcards = list(tree.recursive_crawl("wildcard_expression"))
    # Explicit CTE / derived-table column lists can rename a wildcard's hidden columns.
    renamed_columns = bool(list(tree.recursive_crawl("cte_column_list"))) or any(
        list(alias.recursive_crawl("bracketed"))
        for alias in tree.recursive_crawl("alias_expression")
    )
    complex_scope = len(clauses) != 1 or bool(list(tree.recursive_crawl("common_table_expression")))
    # PIVOT / UNPIVOT and dialect-specific structural rewrites need a dedicated resolver.
    transforms = any(
        "pivot" in segment.type or segment.is_type("values_clause")
        for segment in tree.recursive_crawl_all()
    )
    if renamed_columns or transforms or (complex_scope and wildcards):
        return MaskingLineage(fallback=None, complex_scope=True)
    if complex_scope or wildcards:
        return MaskingLineage(
            fallback=None if _unknown_expression(tree) else _sources(tree),
            complex_scope=complex_scope,
        )
    elements = list(clauses[0].recursive_crawl("select_clause_element"))
    if not elements:
        return MaskingLineage(fallback=None)
    return MaskingLineage(
        projections=tuple(
            None if _unknown_expression(item) else _sources(item) for item in elements
        )
    )
