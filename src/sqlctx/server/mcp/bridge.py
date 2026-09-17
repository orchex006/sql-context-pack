"""Per-Codex-session STDIO bridge for the persistent loopback MCP service."""

from __future__ import annotations

import copy
import json
import secrets
from typing import Any, Protocol

import anyio
import httpx
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl

from sqlctx._version import __version__
from sqlctx.cli.main import _connection


class UpstreamSession(Protocol):
    async def list_tools(self) -> types.ListToolsResult: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> types.CallToolResult: ...


def _profile_tool(name: str, description: str, *, requires_profile: bool) -> types.Tool:
    properties: dict[str, Any] = {}
    required: list[str] = []
    if requires_profile:
        properties["profile"] = {
            "type": "string",
            "minLength": 1,
            "description": "Exact safe profile name returned by sqlctx_list_profiles.",
        }
        required.append("profile")
    return types.Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {
                "connected": {"type": "boolean"},
                "active_profile": {"type": ["string", "null"]},
                "previous_profile": {"type": ["string", "null"]},
            },
            "required": ["connected", "active_profile", "previous_profile"],
            "additionalProperties": False,
        },
    )


class SessionProfileRouter:
    """Decorate one upstream MCP session with isolated active-profile state."""

    LOCAL_TOOLS = (
        _profile_tool(
            "sqlctx_connect_profile",
            "Test and activate one database profile for this MCP session.",
            requires_profile=True,
        ),
        _profile_tool(
            "sqlctx_change_profile",
            "Test and atomically replace this MCP session's active profile.",
            requires_profile=True,
        ),
        _profile_tool(
            "sqlctx_disconnect_profile",
            "Forget the active profile for this MCP session without cancelling retained jobs.",
            requires_profile=False,
        ),
        _profile_tool(
            "sqlctx_get_active_profile",
            "Return the safe active profile name for this MCP session.",
            requires_profile=False,
        ),
    )

    def __init__(self, upstream: UpstreamSession) -> None:
        self.upstream = upstream
        self.active_profile: str | None = None
        self.session_cache_key = "sess_" + secrets.token_urlsafe(24)

    async def list_tools(self) -> list[types.Tool]:
        remote = await self.upstream.list_tools()
        tools: list[types.Tool] = []
        for item in remote.tools:
            if item.name not in {"sqlctx_create_catalog", "sqlctx_query_data"}:
                tools.append(item)
                continue
            adjusted = item.model_copy(deep=True)
            schema = copy.deepcopy(adjusted.inputSchema)
            required = list(schema.get("required", []))
            omitted = (
                {"profile", "idempotency_key"}
                if item.name == "sqlctx_create_catalog"
                else {"profile"}
            )
            schema["required"] = [name for name in required if name not in omitted]
            if item.name == "sqlctx_create_catalog":
                schema.setdefault("properties", {}).pop("session_cache_key", None)
                schema.setdefault("properties", {}).pop("idempotency_key", None)
            profile_schema = schema.setdefault("properties", {}).setdefault("profile", {})
            profile_schema["description"] = (
                "Optional explicit profile. Omit to use this session's active profile."
            )
            adjusted.inputSchema = schema
            tools.append(adjusted)
        return [*tools, *self.LOCAL_TOOLS]

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult | dict[str, Any]:
        if name == "sqlctx_get_active_profile":
            return self._state(previous=self.active_profile)
        if name == "sqlctx_disconnect_profile":
            previous = self.active_profile
            self.active_profile = None
            return self._state(previous=previous)
        if name in {"sqlctx_connect_profile", "sqlctx_change_profile"}:
            requested = str(arguments.get("profile", "")).strip()
            if not requested:
                return self._error(
                    "PROFILE_SELECTION_REQUIRED", "Select an exact safe profile name."
                )
            previous = self.active_profile
            if name == "sqlctx_connect_profile" and previous not in {None, requested}:
                return self._error(
                    "PROFILE_CONTEXT_CONFLICT",
                    "A different profile is active; use sqlctx_change_profile explicitly.",
                )
            tested = await self.upstream.call_tool("sqlctx_test_profile", {"profile": requested})
            if tested.isError:
                return tested
            self.active_profile = requested
            return self._state(previous=previous)
        if name in {"sqlctx_create_catalog", "sqlctx_query_data"}:
            forwarded = dict(arguments)
            explicit = str(forwarded.get("profile", "")).strip() or None
            if explicit is None and self.active_profile is None:
                return self._error(
                    "PROFILE_NOT_CONNECTED",
                    "Connect a profile or pass an explicit profile before database work.",
                )
            if explicit and self.active_profile and explicit != self.active_profile:
                return self._error(
                    "PROFILE_CONTEXT_CONFLICT",
                    "The explicit profile differs from this session's active profile.",
                )
            forwarded["profile"] = explicit or self.active_profile
            if name == "sqlctx_create_catalog":
                forwarded["session_cache_key"] = self.session_cache_key
                forwarded["idempotency_key"] = "bridge_" + secrets.token_urlsafe(24)
            return await self.upstream.call_tool(name, forwarded)
        return await self.upstream.call_tool(name, arguments)

    def _state(self, *, previous: str | None) -> dict[str, Any]:
        return {
            "connected": self.active_profile is not None,
            "active_profile": self.active_profile,
            "previous_profile": previous,
        }

    @staticmethod
    def _error(code: str, message: str) -> types.CallToolResult:
        payload = {"error": {"code": code, "message": message}}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
            isError=True,
        )


async def _run_bridge() -> None:
    base_url, token = _connection()
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=30.0)
    async with httpx.AsyncClient(
        headers=headers, timeout=timeout, trust_env=False, follow_redirects=False
    ) as http_client:
        async with streamable_http_client(base_url + "/mcp", http_client=http_client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as upstream:
                await upstream.initialize()
                router = SessionProfileRouter(upstream)
                server: Server[None] = Server("sql-context-pack-session-bridge")

                @server.list_tools()
                async def list_tools() -> list[types.Tool]:
                    return await router.list_tools()

                @server.call_tool(validate_input=True)
                async def call_tool(
                    name: str, arguments: dict[str, Any]
                ) -> types.CallToolResult | dict[str, Any]:
                    return await router.call_tool(name, arguments)

                @server.list_resources()
                async def list_resources(
                    request: types.ListResourcesRequest,
                ) -> types.ListResourcesResult:
                    return await upstream.list_resources(params=request.params)

                @server.list_resource_templates()
                async def list_resource_templates() -> list[types.ResourceTemplate]:
                    return (await upstream.list_resource_templates()).resourceTemplates

                @server.read_resource()
                async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
                    result = await upstream.read_resource(uri)
                    contents: list[ReadResourceContents] = []
                    for item in result.contents:
                        if isinstance(item, types.TextResourceContents):
                            contents.append(
                                ReadResourceContents(
                                    content=item.text,
                                    mime_type=item.mimeType,
                                    meta=item.meta,
                                )
                            )
                        else:
                            contents.append(
                                ReadResourceContents(
                                    content=item.blob.encode(),
                                    mime_type=item.mimeType,
                                    meta=item.meta,
                                )
                            )
                    return contents

                async with stdio_server() as (stdin, stdout):
                    await server.run(
                        stdin,
                        stdout,
                        InitializationOptions(
                            server_name="sql-context-pack-session-bridge",
                            server_version=__version__,
                            capabilities=server.get_capabilities(
                                notification_options=NotificationOptions(),
                                experimental_capabilities={},
                            ),
                        ),
                    )


def run() -> None:
    """Run the per-session bridge over STDIO."""
    anyio.run(_run_bridge)


if __name__ == "__main__":
    run()
