from __future__ import annotations

from typing import Any

import mcp.types as types
import pytest

from sqlctx.server.mcp.bridge import SessionProfileRouter


class FakeUpstream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.test_failures: set[str] = set()
        self.tool_failures: set[str] = set()

    async def list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="sqlctx_create_catalog",
                    description="create",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "profile": {"type": "string"},
                            "schemas": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["profile", "schemas"],
                        "additionalProperties": False,
                    },
                ),
                types.Tool(
                    name="sqlctx_query_data",
                    description="query",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "profile": {"type": "string"},
                            "sql": {"type": "string"},
                            "max_rows": {"type": "integer"},
                        },
                        "required": ["profile", "sql"],
                        "additionalProperties": False,
                    },
                ),
                types.Tool(
                    name="sqlctx_list_profiles",
                    description="list",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]
        )

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> types.CallToolResult:
        payload = arguments or {}
        self.calls.append((name, payload))
        if name == "sqlctx_test_profile" and payload.get("profile") in self.test_failures:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="unreachable")], isError=True
            )
        if name in self.tool_failures:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="isolated failure")], isError=True
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")], isError=False
        )


@pytest.mark.asyncio
async def test_connect_inject_change_failure_and_disconnect_are_session_scoped() -> None:
    upstream = FakeUpstream()
    router = SessionProfileRouter(upstream)

    missing = await router.call_tool("sqlctx_create_catalog", {"schemas": ["app"]})
    assert isinstance(missing, types.CallToolResult) and missing.isError
    assert "PROFILE_NOT_CONNECTED" in missing.content[0].text  # type: ignore[union-attr]

    connected = await router.call_tool("sqlctx_connect_profile", {"profile": "one"})
    assert connected["active_profile"] == "one"  # type: ignore[index]

    created = await router.call_tool("sqlctx_create_catalog", {"schemas": ["app"]})
    assert isinstance(created, types.CallToolResult) and not created.isError
    call_name, call_payload = upstream.calls[-1]
    assert call_name == "sqlctx_create_catalog"
    assert call_payload["schemas"] == ["app"]
    assert call_payload["profile"] == "one"
    assert call_payload["session_cache_key"].startswith("sess_")
    assert call_payload["idempotency_key"].startswith("bridge_")

    conflict = await router.call_tool(
        "sqlctx_create_catalog", {"schemas": ["app"], "profile": "two"}
    )
    assert isinstance(conflict, types.CallToolResult) and conflict.isError
    assert "PROFILE_CONTEXT_CONFLICT" in conflict.content[0].text  # type: ignore[union-attr]

    upstream.test_failures.add("bad")
    failed_change = await router.call_tool("sqlctx_change_profile", {"profile": "bad"})
    assert isinstance(failed_change, types.CallToolResult) and failed_change.isError
    assert router.active_profile == "one"

    changed = await router.call_tool("sqlctx_change_profile", {"profile": "two"})
    assert changed["active_profile"] == "two"  # type: ignore[index]
    assert changed["previous_profile"] == "one"  # type: ignore[index]

    disconnected = await router.call_tool("sqlctx_disconnect_profile", {})
    assert disconnected["connected"] is False  # type: ignore[index]
    assert disconnected["previous_profile"] == "two"  # type: ignore[index]


@pytest.mark.asyncio
async def test_bridge_tool_schema_makes_profile_optional_and_adds_session_tools() -> None:
    router = SessionProfileRouter(FakeUpstream())
    tools = {item.name: item for item in await router.list_tools()}

    assert "profile" not in tools["sqlctx_create_catalog"].inputSchema["required"]
    assert "session_cache_key" not in tools["sqlctx_create_catalog"].inputSchema["properties"]
    assert "idempotency_key" not in tools["sqlctx_create_catalog"].inputSchema["properties"]
    assert "profile" not in tools["sqlctx_query_data"].inputSchema["required"]
    assert "sql" in tools["sqlctx_query_data"].inputSchema["required"]
    assert {
        "sqlctx_connect_profile",
        "sqlctx_change_profile",
        "sqlctx_disconnect_profile",
        "sqlctx_get_active_profile",
    } <= tools.keys()


@pytest.mark.asyncio
async def test_query_tool_injects_only_active_profile_and_preserves_catalog_routing() -> None:
    upstream = FakeUpstream()
    router = SessionProfileRouter(upstream)
    await router.call_tool("sqlctx_connect_profile", {"profile": "one"})

    queried = await router.call_tool(
        "sqlctx_query_data", {"sql": "SELECT * FROM dbo.CONTENT", "max_rows": 10}
    )
    assert isinstance(queried, types.CallToolResult) and not queried.isError
    assert upstream.calls[-1] == (
        "sqlctx_query_data",
        {"sql": "SELECT * FROM dbo.CONTENT", "max_rows": 10, "profile": "one"},
    )

    conflict = await router.call_tool("sqlctx_query_data", {"sql": "SELECT 1", "profile": "two"})
    assert isinstance(conflict, types.CallToolResult) and conflict.isError
    assert "PROFILE_CONTEXT_CONFLICT" in conflict.content[0].text  # type: ignore[union-attr]

    created = await router.call_tool("sqlctx_create_catalog", {"schemas": ["app"]})
    assert isinstance(created, types.CallToolResult) and not created.isError
    _, catalog_payload = upstream.calls[-1]
    assert catalog_payload["profile"] == "one"
    assert catalog_payload["session_cache_key"].startswith("sess_")
    assert catalog_payload["idempotency_key"].startswith("bridge_")


@pytest.mark.asyncio
async def test_query_failure_preserves_session_state_and_old_tool_routing() -> None:
    upstream = FakeUpstream()
    router = SessionProfileRouter(upstream)
    await router.call_tool("sqlctx_connect_profile", {"profile": "one"})
    upstream.tool_failures.add("sqlctx_query_data")

    failed = await router.call_tool("sqlctx_query_data", {"sql": "SELECT 1"})
    assert isinstance(failed, types.CallToolResult) and failed.isError
    assert router.active_profile == "one"

    listed = await router.call_tool("sqlctx_list_profiles", {})
    assert isinstance(listed, types.CallToolResult) and not listed.isError
    assert router.active_profile == "one"
