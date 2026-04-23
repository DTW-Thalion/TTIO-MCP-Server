"""MCP server entry point for ``mpeg-o-mcp``.

M1 registers zero tools; the server only answers the ``initialize``
handshake. Tool handlers land in M2.
"""
from __future__ import annotations

import asyncio

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from mpeg_o_mcp import __version__


def build_server() -> Server:
    return Server(name="mpeg-o-mcp", version=__version__)


async def serve() -> None:
    server = build_server()
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
