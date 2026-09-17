"""Copy-ready GitHub-flavored Markdown rendering primitives."""

from __future__ import annotations

import re
from typing import Any

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def display_names(names: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for raw in names:
        base = raw or "column"
        key = base.casefold()
        counts[key] = counts.get(key, 0) + 1
        result.append(base if counts[key] == 1 else f"{base}_{counts[key]}")
    return result


def escape_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = text.replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")
    return _CONTROL.sub("�", text)


def header_lines(names: list[str]) -> tuple[str, str]:
    return (
        "| " + " | ".join(escape_cell(name) for name in names) + " |",
        "| " + " | ".join("---" for _ in names) + " |",
    )


def row_line(values: list[Any]) -> str:
    return "| " + " | ".join(escape_cell(value) for value in values) + " |"
