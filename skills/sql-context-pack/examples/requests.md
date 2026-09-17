# Request examples

Requests arrive in English or Thai. Both are equally valid, and the Thai examples below are kept
deliberately: recognising them is a product requirement, not decoration.

## English

- `Build all database context under .agent/context/database using profile reporting-readonly.`
- `Build only the final categories um and content under ./sql-context.`
- `Resume the exact retained export for this request and validate the assembled output.`
- `Show me every table whose context is still unresolved, with your suggestion for each.`

## Thai

- `สร้าง SQL context จาก profile agrimap-readonly ไป ./docs/sql-context แล้วถามก่อนเลือก category`
  — build SQL context from that profile into that path, asking before choosing a category.
- `สร้างเฉพาะ final categories um และ content ไป ./sql-context`
  — build only those final categories into that path.
- `สร้างทั้งหมด` / `dump ทุกหมวด` — build everything; equivalent to `selection.mode=all`.
- `เอาเฉพาะ um กับ content` — only those categories.

## Resolving the output path

An explicit output path in the request wins. Without one, use the configured `default_output`;
otherwise use the repository root plus `sql-context/`.

Never invent an absolute path, and never accept one from the agent — the owner registers paths
from their own terminal.
