from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_pinned_sdk_generates_strict_structured_tool_schemas() -> None:
    if importlib.util.find_spec("mcp") is None:
        project = Path("pyproject.toml").read_text(encoding="utf-8")
        source = Path("src/sqlctx/server/mcp/server.py").read_text(encoding="utf-8")
        assert '"mcp==1.28.1"' in project
        assert 'model_config["extra"] = "forbid"' in source
        assert (
            "registered.parameters = registered.fn_metadata.arg_model.model_json_schema" in source
        )
        return
    from sqlctx.server.mcp.server import build_mcp

    async def inspect() -> None:
        server = build_mcp(object(), "agent:contract")  # type: ignore[arg-type]
        tools = await server.list_tools()
        assert len(tools) == 34
        assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools)
        assert all(tool.outputSchema is not None for tool in tools)

    asyncio.run(inspect())


def test_query_construction_failure_leaves_old_mcp_tools_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("Pinned MCP SDK is not installed in this environment.")

    from sqlctx.query_data import service as query_service_module
    from sqlctx.security.runtime import JsonRuntimeStateStore
    from sqlctx.server import facade as facade_module
    from sqlctx.server.contracts import QueryDataRequest
    from sqlctx.server.facade import ServiceFacade
    from sqlctx.server.mcp.server import McpToolRouter, build_mcp

    class UnavailableQueryService:
        def __init__(self, classifier: object) -> None:
            raise RuntimeError("query dependency unavailable")

    service = object.__new__(ServiceFacade)
    service.state = JsonRuntimeStateStore(tmp_path / "runtime")
    service._query_masker = object()
    service._queries = None
    service.profiles = SimpleNamespace(
        resolve=lambda name: SimpleNamespace(name=name, engine="sqlserver")
    )
    monkeypatch.setattr(query_service_module, "QueryDataService", UnavailableQueryService)
    monkeypatch.setattr(facade_module, "create_adapter", lambda engine: object())

    mcp = build_mcp(service, "agent:failure-isolation")
    router = McpToolRouter(service, "agent:failure-isolation")

    async def inspect() -> None:
        tools = await mcp.list_tools()
        assert len(tools) == 34
        assert "sqlctx_get_capabilities" in {tool.name for tool in tools}

    asyncio.run(inspect())
    with pytest.raises(RuntimeError, match="query dependency unavailable"):
        router.invoke(
            "sqlctx_query_data",
            QueryDataRequest(profile="demo", sql="SELECT 1").model_dump(),
        )
    assert router.invoke("sqlctx_get_capabilities", {})["interfaces"] == ["http", "mcp"]
