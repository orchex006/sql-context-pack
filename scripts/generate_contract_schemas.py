"""Generate checked-in HTTP/MCP schema artifacts from runtime models."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# These imports must follow the checkout-first path bootstrap above.
from sqlctx.security.profiles import YamlConnectionProfileRepository  # noqa: E402
from sqlctx.security.runtime import JsonRuntimeStateStore  # noqa: E402
from sqlctx.server.facade import ServiceFacade  # noqa: E402
from sqlctx.server.http.app import create_app  # noqa: E402
from sqlctx.server.mcp.bridge import SessionProfileRouter  # noqa: E402
from sqlctx.server.mcp.server import build_mcp  # noqa: E402


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not reference:
        return schema
    value: Any = root
    for part in str(reference).removeprefix("#/").split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value if isinstance(value, dict) else {}


def _string_example(name: str, schema: dict[str, Any]) -> str:
    lowered = name.lower()
    if "sha256" in lowered or "fingerprint" in lowered or lowered.endswith("digest"):
        return "sha256:" + "0" * 64
    if lowered == "catalog_id":
        return "cat_example"
    if lowered == "export_id":
        return "exp_example"
    if lowered == "object_id":
        return "obj_example"
    if lowered == "profile" or lowered == "profile_name":
        return "demo"
    if lowered in {"schema", "schema_name"}:
        return "app"
    if lowered == "cursor":
        return "cursor_example"
    if "idempotency" in lowered:
        return "idem_example"
    if lowered == "python_version":
        return "3.11.0"
    if lowered == "sqlfluff_version":
        return "4.2.2"
    if lowered == "output_format_version":
        return "1"
    if lowered == "version":
        return "1.6.0"
    if lowered.endswith("_url"):
        return "http://127.0.0.1:8765/example"
    return f"{lowered or 'value'}_example"


def _example(schema: dict[str, Any], root: dict[str, Any], name: str = "value") -> Any:
    schema = _resolve(schema, root)
    if "const" in schema:
        return schema["const"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if alternatives:
        selected = next(
            (item for item in alternatives if item.get("type") != "null"), alternatives[0]
        )
        return _example(selected, root, name)
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties))
        return {key: _example(properties[key], root, key) for key in required if key in properties}
    if schema_type == "array":
        return [_example(schema.get("items", {}), root, name)]
    if schema_type == "integer":
        return max(int(schema.get("minimum", 0)), 1)
    if schema_type == "number":
        return max(float(schema.get("minimum", 0)), 0.5)
    if schema_type == "boolean":
        return True
    if schema_type == "string" or not schema_type:
        return _string_example(name, schema)
    return None


def _add_http_examples(openapi: dict[str, Any]) -> None:
    for path, path_item in openapi["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "delete"}:
                continue
            parameters = {
                item["name"]: _example(item.get("schema", {}), openapi, item["name"])
                for item in operation.get("parameters", [])
            }
            request_example = None
            request_content = operation.get("requestBody", {}).get("content", {})
            if request_content:
                request_schema = next(iter(request_content.values())).get("schema", {})
                request_example = _example(request_schema, openapi, "request")
            success = next(
                (
                    (status, response)
                    for status, response in operation.get("responses", {}).items()
                    if str(status).startswith("2")
                ),
                ("200", {}),
            )
            response_content = success[1].get("content", {})
            if "application/zip" in response_content:
                response_example: Any = {
                    "media_type": "application/zip",
                    "transfer": "Use authenticated sqlctx export fetch; binary is not inlined.",
                }
            elif response_content:
                response_schema = next(iter(response_content.values())).get("schema", {})
                response_example = _example(response_schema, openapi, "response")
            else:
                response_example = None
            operation["x-sqlctx-examples"] = {
                "parameters": parameters,
                "request": request_example,
                "response": {"status": success[0], "body": response_example},
                "path": path,
            }


def _add_mcp_examples(mcp: dict[str, Any]) -> None:
    for tool in mcp["tools"]:
        input_schema = tool["inputSchema"]
        output_schema = tool["outputSchema"]
        tool["inputExample"] = _example(input_schema, input_schema, "arguments")
        tool["outputExample"] = _example(output_schema, output_schema, "result")


async def mcp_schema(facade: ServiceFacade) -> dict[str, Any]:
    server = build_mcp(facade, "agent:schema-generation")
    tools = await server.list_tools()
    templates = await server.list_resource_templates()
    return {
        "tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools],
        "resource_templates": [item.model_dump(mode="json", by_alias=True) for item in templates],
    }


def main() -> None:
    output = ROOT / "docs/generated"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sqlctx-schema-") as temporary:
        runtime = Path(temporary)
        state = JsonRuntimeStateStore(runtime)
        facade = ServiceFacade(
            state=state,
            profiles=YamlConnectionProfileRepository(runtime / "profiles.yaml", {}),
            categories_path=ROOT / "config/categories.yaml",
            overrides_path=runtime / "category-overrides.yaml",
        )
        app = create_app(facade)
        openapi = app.openapi()
        mcp = asyncio.run(mcp_schema(facade))
        bridge = {
            "tools": [
                tool.model_dump(mode="json", by_alias=True)
                for tool in SessionProfileRouter.LOCAL_TOOLS
            ],
            "catalog_profile_injection": {
                "tool": "sqlctx_create_catalog",
                "profile_required_by_bridge": False,
                "missing_error": "PROFILE_NOT_CONNECTED",
                "conflict_error": "PROFILE_CONTEXT_CONFLICT",
            },
            "query_profile_injection": {
                "tool": "sqlctx_query_data",
                "profile_required_by_bridge": False,
                "missing_error": "PROFILE_NOT_CONNECTED",
                "conflict_error": "PROFILE_CONTEXT_CONFLICT",
            },
        }
        _add_http_examples(openapi)
        _add_mcp_examples(mcp)
        _add_mcp_examples(bridge)
    (output / "openapi.json").write_text(
        json.dumps(openapi, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "mcp-tools.json").write_text(
        json.dumps(mcp, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "mcp-bridge-tools.json").write_text(
        json.dumps(bridge, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"Generated {len(openapi['paths'])} HTTP paths, {len(mcp['tools'])} core MCP tools, "
        f"and {len(bridge['tools'])} bridge tools."
    )


if __name__ == "__main__":
    main()
