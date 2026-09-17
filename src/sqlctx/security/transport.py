"""Validate the local trust boundary before attaching bearer credentials."""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlctx.core.errors import SqlCtxError


def local_mcp_url(value: object) -> str:
    """Accept only the exact service endpoint; never resolve a caller-controlled hostname."""
    try:
        if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
            raise ValueError
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or port is None
            or not 1 <= port <= 65535
            or parsed.netloc != f"127.0.0.1:{port}"
            or parsed.path != "/mcp"
            or parsed.query
            or parsed.fragment
            or value != f"http://127.0.0.1:{port}/mcp"
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise SqlCtxError(
            "UNSAFE_SERVICE_ENDPOINT",
            "Connection metadata must use http://127.0.0.1:<port>/mcp. Run sqlctx doctor check.",
        ) from exc
    return value


def agent_connection(value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise SqlCtxError("SERVER_METADATA_INVALID", "Connection metadata is invalid.")
    endpoint = local_mcp_url(value.get("mcp_url"))
    token = value.get("agent_token")
    if (
        not isinstance(token, str)
        or not token
        or any(ord(char) <= 32 or ord(char) >= 127 for char in token)
    ):
        raise SqlCtxError("SERVER_METADATA_INVALID", "Agent authentication metadata is invalid.")
    return endpoint.removesuffix("/mcp"), token
