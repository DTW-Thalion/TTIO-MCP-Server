from __future__ import annotations

import shutil
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_initialize_handshake() -> None:
    command = shutil.which("ttio-mcp") or sys.executable
    args: list[str] = []
    if command == sys.executable:
        args = ["-m", "ttio_mcp.server"]

    params = StdioServerParameters(command=command, args=args)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()

    assert result.protocolVersion
    assert result.capabilities is not None
    assert result.serverInfo.name == "ttio-mcp"
