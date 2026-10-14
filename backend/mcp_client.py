"""Small stdio client for the shared shop-data MCP and isolated approval MCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = PROJECT_ROOT / "mcp_server"
MCP_PYTHON = MCP_DIR / ".venv" / "bin" / "python"
SHOP_SERVER = MCP_DIR / "server.py"
APPROVAL_SERVER = MCP_DIR / "approval_server.py"


class MCPToolError(RuntimeError):
    """An MCP tool returned an application-level error result."""


async def call_mcp_tool(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    approval_server: bool = False,
    workflow_id: str | None = None,
) -> Any:
    """Call one tool over stdio MCP; shop database access stays in MCP processes."""
    server_file = APPROVAL_SERVER if approval_server else SHOP_SERVER
    if not server_file.is_file():
        raise FileNotFoundError(f"MCP server not found: {server_file}")
    command = str(MCP_PYTHON if MCP_PYTHON.is_file() else Path(sys.executable))
    child_environment = os.environ.copy()
    child_environment.pop("PORTKEY_API_KEY", None)
    if workflow_id:
        child_environment["CAMPUS_CUSTOMS_WORKFLOW_ID"] = workflow_id
    parameters = StdioServerParameters(
        command=command,
        args=[str(server_file)],
        env=child_environment,
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                tool_name,
                arguments or {},
                read_timeout_seconds=timedelta(seconds=45),
            )
    if result.isError:
        message = "\n".join(
            getattr(block, "text", "") for block in result.content
        ).strip()
        raise MCPToolError(message or f"MCP tool {tool_name} failed")
    if result.structuredContent is not None:
        structured = result.structuredContent
        # FastMCP encodes some list outputs as {"result": [...]} while
        # object outputs are returned directly. Present one consistent value
        # to API routes and agent orchestration callers.
        if (
            isinstance(structured, dict)
            and set(structured) == {"result"}
            and isinstance(structured["result"], list)
        ):
            return structured["result"]
        return structured
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None
