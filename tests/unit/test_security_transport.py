from __future__ import annotations

import httpx
import pytest

from sqlctx.core.errors import SqlCtxError
from sqlctx.security.transport import agent_connection, local_mcp_url


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://outside.invalid/mcp",
        "http://localhost:8765/mcp",
        "http://127.1:8765/mcp",
        "http://127.0.0.1:8765/mcp?x=1",
        "http://127.0.0.1:8765/mcp#x",
        "http://user@127.0.0.1:8765/mcp",
        "http://127.0.0.1:0/mcp",
        "http://127.0.0.1:65536/mcp",
        "http://127.0.0.1:8765/mcp/",
        "http://127.0.0.1:8765/other",
        "http://127.0.0.1:8765/mcp\n",
        "http://127.0.0.1:8765/mcp?",
        "http://127.0.0.1:8765/mcp#",
        None,
    ],
)
def test_unsafe_endpoint_fails_before_token_attachment(endpoint: object) -> None:
    with pytest.raises(SqlCtxError) as caught:
        agent_connection({"mcp_url": endpoint, "agent_token": "fixture-token"})
    assert caught.value.code == "UNSAFE_SERVICE_ENDPOINT"
    assert "fixture-token" not in str(caught.value)


def test_custom_loopback_port_is_preserved() -> None:
    assert local_mcp_url("http://127.0.0.1:9876/mcp") == "http://127.0.0.1:9876/mcp"


@pytest.mark.parametrize("token", [None, "", "bad\r\nheader", "bad token"])
def test_invalid_bearer_is_rejected(token: object) -> None:
    with pytest.raises(SqlCtxError):
        agent_connection({"mcp_url": "http://127.0.0.1:8765/mcp", "agent_token": token})


def test_doctor_does_not_follow_redirect_or_use_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlctx import doctor

    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid:8765")
    monkeypatch.setattr(
        doctor.JsonRuntimeStateStore,
        "read_json",
        lambda *args: {"mcp_url": "http://127.0.0.1:8765/mcp", "agent_token": "fixture-token"},
    )
    real_client = httpx.Client
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://untrusted.invalid/"})

    def client(**kwargs: object) -> httpx.Client:
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(respond), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(doctor.httpx, "Client", client)
    result = doctor.inspect_installation()
    assert len(seen) == 1
    assert seen[0].url.host == "127.0.0.1"
    assert any(item["code"] == "SERVICE_UNAVAILABLE" for item in result["findings"])
