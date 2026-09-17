import pytest

from sqlctx.core.errors import ApprovalRequired, SqlCtxError
from sqlctx.security.approvals import ApprovalService


def test_approval_is_bound_and_single_use() -> None:
    service = ApprovalService()
    payload = {"version": "4.2.2"}
    challenge = service.challenge(
        caller="agent-a", operation="sqlctx_sqlfluff_update", target="tooling", payload=payload
    )
    with pytest.raises(SqlCtxError) as noninteractive:
        service.grant(challenge.challenge_id, interactive=False)
    assert noninteractive.value.code == "OWNER_PRESENCE_REQUIRED"

    service.grant(challenge.challenge_id, interactive=True)
    with pytest.raises(ApprovalRequired):
        service.consume(
            challenge.challenge_id,
            caller="agent-b",
            operation="sqlctx_sqlfluff_update",
            target="tooling",
            payload=payload,
        )
    service.consume(
        challenge.challenge_id,
        caller="agent-a",
        operation="sqlctx_sqlfluff_update",
        target="tooling",
        payload=payload,
    )
    with pytest.raises(ApprovalRequired):
        service.consume(
            challenge.challenge_id,
            caller="agent-a",
            operation="sqlctx_sqlfluff_update",
            target="tooling",
            payload=payload,
        )


def test_expired_approval_has_visible_status_and_retry_guidance() -> None:
    service = ApprovalService(ttl_seconds=-1)
    challenge = service.challenge(
        caller="agent", operation="catalog.delete", target="cat_1", payload={"catalog_id": "cat_1"}
    )

    listed = service.list_challenges()
    assert listed[0]["challenge_id"] == challenge.challenge_id
    assert listed[0]["status"] == "expired"
    with pytest.raises(SqlCtxError) as caught:
        service.grant(challenge.challenge_id, interactive=True)
    assert caught.value.code == "APPROVAL_EXPIRED"
    assert "Retry the original operation" in caught.value.message
    assert service.cleanup_expired(retain_seconds=0) == [challenge.challenge_id]
    assert service.list_challenges() == []
