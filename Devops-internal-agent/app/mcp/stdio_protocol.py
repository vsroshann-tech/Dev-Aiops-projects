"""Shared MCP (Model Context Protocol) JSON-RPC 2.0 stdio server runtime."""

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass
class MCPToolDef:
    """Metadata and handler for an MCP tool."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[..., Dict[str, Any]]

    def to_mcp_schema(self) -> Dict[str, Any]:
        """Return MCP-compliant tool descriptor."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def run_stdio_server(server_name: str, tools: List[MCPToolDef]) -> None:
    """Run a JSON-RPC 2.0 MCP server over stdin/stdout."""
    tool_map = {t.name: t for t in tools}

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _write_response(None, error={"code": -32700, "message": "Parse error"})
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        if method == "initialize":
            _write_response(
                req_id,
                result={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": server_name, "version": "0.1.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _write_response(
                req_id,
                result={"tools": [t.to_mcp_schema() for t in tools]},
            )
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            tool = tool_map.get(tool_name)
            if not tool:
                _write_response(
                    req_id,
                    error={"code": -32601, "message": f"Unknown tool: {tool_name}"},
                )
                continue
            try:
                output = tool.handler(**arguments)
                _write_response(
                    req_id,
                    result={
                        "content": [
                            {"type": "text", "text": json.dumps(output, indent=2)}
                        ],
                        "isError": bool(output.get("blocked") or output.get("error")),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                _write_response(
                    req_id,
                    result={
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                )
        else:
            if req_id is not None:
                _write_response(
                    req_id,
                    error={"code": -32601, "message": f"Method not found: {method}"},
                )


def _write_response(req_id: Any, result: Any = None, error: Any = None) -> None:
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
