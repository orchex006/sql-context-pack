"""Block inference of protected values through predicates, grouping, ordering and functions.

Masking only protects what a query returns. `WHERE first_name LIKE 'A%'`, `ORDER BY
citizen_id` or `SELECT LEN(phone)` would let a caller recover a protected value one bit at
a time, so a protected column may appear only as a plain projection.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlctx.query_data.lineage import _identifier

_RESTRICTED_CONTEXTS = frozenset(
    {
        "where_clause",
        "join_on_condition",
        "using_clause",
        "groupby_clause",
        "orderby_clause",
        "having_clause",
        "qualify_clause",
        "window_specification",
        "over_clause",
        "function",
        "case_expression",
    }
)


def _reference_name(reference: Any) -> str | None:
    identifiers = [item.raw for item in reference.raw_segments if item.is_type("identifier")]
    return _identifier(identifiers[-1]) if identifiers else None


def _alias_name(element: Any) -> str | None:
    for alias in element.recursive_crawl("alias_expression"):
        identifiers = [item.raw for item in alias.raw_segments if item.is_type("identifier")]
        if identifiers:
            return _identifier(identifiers[-1])
    return None


def restricted_sensitive_columns(tree: Any, is_sensitive: Callable[[str], bool]) -> list[str]:
    """Return protected column names used outside a plain projection, sorted; empty if none."""
    references = [
        (reference, name)
        for reference in tree.recursive_crawl("column_reference")
        if (name := _reference_name(reference)) is not None
    ]
    sensitive = {name.casefold() for _, name in references if is_sensitive(name)}
    if not sensitive:
        return []
    # An alias of a protected column is protected too: `SELECT first_name AS x ... WHERE x`.
    changed = True
    while changed:
        changed = False
        for element in tree.recursive_crawl("select_clause_element"):
            alias = _alias_name(element)
            if alias is None or alias.casefold() in sensitive:
                continue
            if any(
                (name := _reference_name(reference)) is not None and name.casefold() in sensitive
                for reference in element.recursive_crawl("column_reference")
            ):
                sensitive.add(alias.casefold())
                changed = True
    # Explicit CTE column lists rename positionally; treat every renamed column as protected.
    for column_list in tree.recursive_crawl("cte_column_list"):
        for item in column_list.raw_segments:
            if item.is_type("identifier"):
                sensitive.add(_identifier(item.raw).casefold())
    restricted: set[str] = set()
    for reference, name in references:
        if name.casefold() not in sensitive:
            continue
        ancestors = {step.segment.type for step in tree.path_to(reference)}
        if ancestors & _RESTRICTED_CONTEXTS:
            restricted.add(name)
    return sorted(restricted, key=str.casefold)
