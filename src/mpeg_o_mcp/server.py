"""MCP server entry point for ``mpeg-o-mcp``.

M2 wires in the four catalog tools (register / list / get / reverify).
The database URL comes from :class:`mpeg_o_mcp.config.Config` and the
schema is expected to be migrated via ``alembic upgrade head`` before
first run.
"""
from __future__ import annotations

import asyncio

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from mpeg_o_mcp import __version__
from mpeg_o_mcp.config import Config
from mpeg_o_mcp.db.session import make_engine, make_session_factory
from mpeg_o_mcp.keyring import Keyring
from mpeg_o_mcp.tools import register as register_tools


def build_server(config: Config | None = None) -> tuple[Server, Config]:
    cfg = config or Config.from_env()
    server = Server(name="mpeg-o-mcp", version=__version__)
    engine = make_engine(cfg.db_url)
    session_factory = make_session_factory(engine)
    keyring = Keyring.from_path(cfg.keyring_path)
    register_tools(server, session_factory, keyring=keyring)
    return server, cfg


async def serve() -> None:
    server, _cfg = build_server()
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
