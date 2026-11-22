"""MCP Client Manager for discovering and invoking DevOps MCP tools."""

import json
import subprocess
import sys
from typing import Any, Dict, List

from app.mcp.servers.aws_infra_server import TOOLS as AWS_TOOLS
from app.mcp.servers.db_diagnostics_server import TOOLS as DB_TOOLS
from app.mcp.servers.github_actions_server import TOOLS as GITHUB_TOOLS
from app.mcp.stdio_protocol import MCPToolDef


class MCPClientManager:
    """Manages DevOps MCP server registries and supports stdio JSON-RPC & in-process calls."""

    def __init__(self) -> None:
        self.server_modules: Dict[str, str] = {
            "github-actions-mcp": "app.mcp.servers.github_actions_server",
            "aws-infra-mcp": "app.mcp.servers.aws_infra_server",
            "db-diagnostics-mcp": "app.mcp.servers.db_diagnostics_server",
        }
        self._tools: Dict[str, tuple[str, MCPToolDef]] = {}
        for server_name, tool_list in [
            ("github-actions-mcp", GITHUB_TOOLS),
            ("aws-infra-mcp", AWS_TOOLS),
            ("db-diagnostics-mcp", DB_TOOLS),
        ]:
            for tool in tool_list:
                self._tools[tool.name] = (server_name, tool)

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all available MCP tools with their server and JSON schema."""
        result = []
        for server_name, tool in self._tools.values():
            schema = tool.to_mcp_schema()
            schema["server"] = server_name
            result.append(schema)
        return result

    def call_tool(
        self, tool_name: str, arguments: Dict[str, Any] | None = None, use_stdio: bool = False
    ) -> Dict[str, Any]:
        """Invoke an MCP tool either directly in-process or via stdio JSON-RPC subprocess."""
        if tool_name not in self._tools:
            return {"error": f"Unknown MCP tool: {tool_name}"}

        server_name, tool_def = self._tools[tool_name]
        args = arguments or {}
        if use_stdio:
            return self._call_via_stdio(self.server_modules[server_name], tool_name, args)

        return tool_def.handler(**args)

    def _call_via_stdio(
        self, module_path: str, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a genuine MCP JSON-RPC 2.0 handshake + tools/call over subprocess stdio."""
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        ]
        stdin_payload = "\n".join(json.dumps(r) for r in requests) + "\n"
        proc = subprocess.run(
            [sys.executable, "-m", module_path],
            input=stdin_payload,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return {"error": f"MCP stdio server failed: {proc.stderr.strip()}"}

        responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        call_resp = next((r for r in responses if r.get("id") == 2), None)
        if not call_resp:
            return {"error": "No tools/call response returned from MCP stdio server."}
        if "error" in call_resp:
            return {"error": call_resp["error"]}

        content = call_resp.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            try:
                return json.loads(content[0]["text"])
            except json.JSONDecodeError:
                return {"raw": content[0]["text"]}
        return call_resp.get("result", {})
