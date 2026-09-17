from pathlib import Path

from sqlctx.exporting.validation import inventory_output


def test_realistic_output_fixture_has_required_layout_and_valid_hashes() -> None:
    root = Path("fixtures/realistic-output")
    required = [
        "um/tables/UM_USER.sql",
        "um/tables/UM_ROLE.sql",
        "content/tables/CONTENT.sql",
        "content/tables/CONTENT_SHARE.sql",
        "um/store_procedures/UM_USER_I.sql",
        "content/store_procedures/CONTENT_I.sql",
        "unresolved/classification-requests.yaml",
        "indexes/relationships.json",
        "indexes/graph.json",
    ]
    assert all((root / path).is_file() for path in required)
    inventory = inventory_output(root)
    assert len(inventory.files) == 26
