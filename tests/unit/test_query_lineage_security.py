from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from sqlctx.adapters.base import QueryColumnMetadata
from sqlctx.query_data.contracts import QueryDataRequest
from sqlctx.query_data.masking import EphemeralQueryMasker

FIXTURE = runpy.run_path(str(Path(__file__).with_name("test_query_data_service.py")))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT access_token AS public_value FROM dbo.CONTENT_SHARE",
        "SELECT public_value = access_token FROM dbo.CONTENT_SHARE",
        "SELECT CONCAT(access_token, 'suffix') AS public_value FROM dbo.CONTENT_SHARE",
        "SELECT SUBSTRING(password, 1, 5) AS public_value FROM dbo.CONTENT_SHARE",
        "SELECT CASE WHEN ID = 1 THEN access_token ELSE 'x' END AS public_value FROM dbo.CONTENT_SHARE",
        "WITH c AS (SELECT access_token AS x FROM dbo.CONTENT_SHARE) SELECT x AS public_value FROM c",
        "WITH c(x) AS (SELECT access_token FROM dbo.CONTENT_SHARE) SELECT x AS public_value FROM c",
        "WITH c(x) AS (SELECT * FROM dbo.CONTENT_SHARE) SELECT x AS public_value FROM c",
        "SELECT x AS public_value FROM (SELECT access_token AS x FROM dbo.CONTENT_SHARE) AS c",
        "SELECT ID AS public_value FROM dbo.CONTENT_SHARE UNION ALL SELECT access_token FROM dbo.CONTENT_SHARE",
        "SELECT JSON_VALUE(config_payload, '$.password') AS public_value FROM dbo.CONTENT_SHARE",
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
def test_secret_cannot_be_declassified_by_query(sql: str, streaming: bool) -> None:
    secret = "syntheticOpaqueCredential_A123"
    adapter = FIXTURE["FakeQueryAdapter"](
        FIXTURE["FakeStream"]([QueryColumnMetadata("public_value", "text")], [[(secret,)]])
    )
    service = FIXTURE["service"]()
    request = QueryDataRequest(profile="demo", sql=sql, value_mode="full")
    if streaming:
        output = "\n".join(
            service.stream_markdown(request, profile=FIXTURE["profile"](), adapter=adapter)
        )
    else:
        output = service.execute(request, profile=FIXTURE["profile"](), adapter=adapter).markdown
    assert secret not in output
    assert "[REDACTED]" in output


def test_public_projection_remains_usable_next_to_secret_alias() -> None:
    adapter = FIXTURE["FakeQueryAdapter"](
        FIXTURE["FakeStream"](
            [QueryColumnMetadata("public_value"), QueryColumnMetadata("item_id")],
            [[("synthetic-secret", 42)]],
        )
    )
    result = FIXTURE["service"]().execute(
        QueryDataRequest(
            profile="demo",
            sql="SELECT access_token AS public_value, ID AS item_id FROM dbo.CONTENT_SHARE",
        ),
        profile=FIXTURE["profile"](),
        adapter=adapter,
    )
    assert "synthetic-secret" not in result.markdown
    assert "| [REDACTED] | 42 |" in result.markdown


def test_sensitive_json_parent_is_not_overridden_by_public_child_keys() -> None:
    masker = EphemeralQueryMasker(FIXTURE["ClassifierOnlyMasker"]())
    assert masker.mask("access_token", '{"value":"synthetic-secret"}') == "[REDACTED]"
    assert "synthetic-secret" not in masker.mask(
        "payload", '{"access_token":{"value":"synthetic-secret"}}'
    )
