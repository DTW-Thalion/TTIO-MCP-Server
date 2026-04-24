"""MCP tool registration for mpeg-o-mcp.

A single ``register`` entry point attaches every tool to the given
lowlevel :class:`mcp.server.lowlevel.Server` instance. Handlers that
need the server-side keyring (``mpgo_encrypt_file``,
``mpgo_decrypt_file``, ``mpgo_get_spectrum``) are dispatched with a
``keyring=`` kwarg so the raw key bytes never leave process memory.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Coroutine
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from sqlalchemy.orm import Session, sessionmaker

from mpeg_o_mcp.catalog import CatalogError
from mpeg_o_mcp.keyring import Keyring, KeyringError
from mpeg_o_mcp.tools.decrypt_file import SCHEMA as DECRYPT_SCHEMA
from mpeg_o_mcp.tools.decrypt_file import handle as handle_decrypt
from mpeg_o_mcp.tools.encrypt_file import SCHEMA as ENCRYPT_SCHEMA
from mpeg_o_mcp.tools.encrypt_file import handle as handle_encrypt
from mpeg_o_mcp.tools.get_file import SCHEMA as GET_SCHEMA
from mpeg_o_mcp.tools.get_file import handle as handle_get
from mpeg_o_mcp.tools.get_quantifications import SCHEMA as GET_QUANT_SCHEMA
from mpeg_o_mcp.tools.get_quantifications import handle as handle_get_quant
from mpeg_o_mcp.tools.get_run import SCHEMA as GET_RUN_SCHEMA
from mpeg_o_mcp.tools.get_run import handle as handle_get_run
from mpeg_o_mcp.tools.get_spectrum import SCHEMA as GET_SPEC_SCHEMA
from mpeg_o_mcp.tools.get_spectrum import handle as handle_get_spec
from mpeg_o_mcp.tools.list_files import SCHEMA as LIST_SCHEMA
from mpeg_o_mcp.tools.list_files import handle as handle_list
from mpeg_o_mcp.tools.push_file import SCHEMA as PUSH_SCHEMA
from mpeg_o_mcp.tools.push_file import handle as handle_push
from mpeg_o_mcp.tools.register import SCHEMA as REGISTER_SCHEMA
from mpeg_o_mcp.tools.register import handle as handle_register
from mpeg_o_mcp.tools.reverify import SCHEMA as REVERIFY_SCHEMA
from mpeg_o_mcp.tools.reverify import handle as handle_reverify
from mpeg_o_mcp.tools.search_identifications import SCHEMA as SEARCH_ID_SCHEMA
from mpeg_o_mcp.tools.search_identifications import handle as handle_search_id
from mpeg_o_mcp.tools.sign_file import SCHEMA as SIGN_SCHEMA
from mpeg_o_mcp.tools.sign_file import handle as handle_sign
from mpeg_o_mcp.tools.verify_signature import SCHEMA as VERIFY_SIG_SCHEMA
from mpeg_o_mcp.tools.verify_signature import handle as handle_verify_sig

Handler = Callable[..., Coroutine[Any, Any, dict[str, Any]]]


TOOLS: list[tuple[str, str, dict[str, Any], Handler]] = [
    (
        "mpgo_register_file",
        "Register an .mpgo file in the catalog. Resolves the URI, hashes the bytes, "
        "extracts metadata, and inserts rows atomically. Re-registering the same URI "
        "updates the file row and replaces child rows.",
        REGISTER_SCHEMA,
        handle_register,
    ),
    (
        "mpgo_list_files",
        "List files in the catalog with optional filters and pagination.",
        LIST_SCHEMA,
        handle_list,
    ),
    (
        "mpgo_get_file",
        "Get the full catalog record for a file, identified by id or uri.",
        GET_SCHEMA,
        handle_get,
    ),
    (
        "mpgo_reverify",
        "Re-hash the referenced file bytes and update last_verified_at. "
        "Returns drift=true if the file_sha256 has changed since registration.",
        REVERIFY_SCHEMA,
        handle_reverify,
    ),
    (
        "mpgo_search_identifications",
        "Search identifications across all registered files. Filter by chebi_id, "
        "name substring, minimum score, acquisition mode, or file. Paginated.",
        SEARCH_ID_SCHEMA,
        handle_search_id,
    ),
    (
        "mpgo_get_run",
        "Get per-run detail: run metadata plus its identifications and any "
        "quantifications scoped to this run (sample_ref == run name or NULL).",
        GET_RUN_SCHEMA,
        handle_get_run,
    ),
    (
        "mpgo_get_spectrum",
        "Read a single spectrum from disk. Returns channel arrays and metadata. "
        "Arrays longer than max_points are downsampled via stride; truncated=true "
        "in the response when this happens.",
        GET_SPEC_SCHEMA,
        handle_get_spec,
    ),
    (
        "mpgo_get_quantifications",
        "List quantifications for a file with optional chebi_id / sample_ref / "
        "min_abundance filters. Paginated.",
        GET_QUANT_SCHEMA,
        handle_get_quant,
    ),
    (
        "mpgo_encrypt_file",
        "Encrypt the intensity channel of every run in-place using AES-256-GCM. "
        "Resolves the server-side key via key_id; raw keys are never passed over MCP. "
        "Local files only.",
        ENCRYPT_SCHEMA,
        handle_encrypt,
    ),
    (
        "mpgo_decrypt_file",
        "Decrypt an encrypted .mpgo in place (persist plaintext back to disk). "
        "Delegates to MPEG-O v1.1.1 SpectralDataset.decrypt_in_place. "
        "Local files only.",
        DECRYPT_SCHEMA,
        handle_decrypt,
    ),
    (
        "mpgo_push_file",
        "Upload a local .mpgo to a writable cloud URI (s3://, gs://, abfs://, ...), "
        "optionally encrypting on the way with an AES-256-GCM key from the server-side "
        "keyring. Registers the uploaded object in the catalog. The local source is "
        "never modified.",
        PUSH_SCHEMA,
        handle_push,
    ),
    (
        "mpgo_sign_file",
        "Sign every signal-channel dataset in a local .mpgo with HMAC-SHA256. "
        "Resolves the key via key_id; raw keys are never transmitted over MCP. "
        "Encrypted files are rejected — decrypt first. Local files only.",
        SIGN_SCHEMA,
        handle_sign,
    ),
    (
        "mpgo_verify_signature",
        "Verify every signed dataset in a local .mpgo against an HMAC-SHA256 key "
        "from the server-side keyring. Returns per-dataset verdicts plus an "
        "aggregate valid flag. Local files only.",
        VERIFY_SIG_SCHEMA,
        handle_verify_sig,
    ),
]


def register(
    server: Server,
    session_factory: sessionmaker[Session],
    *,
    keyring: Keyring | None = None,
) -> None:
    active_keyring = keyring or Keyring.from_env()
    tool_defs = [
        types.Tool(name=name, description=desc, inputSchema=schema)
        for (name, desc, schema, _handler) in TOOLS
    ]
    name_to_handler: dict[str, Handler] = {
        name: handler for (name, _d, _s, handler) in TOOLS
    }
    name_wants_keyring: dict[str, bool] = {
        name: "keyring" in inspect.signature(handler).parameters
        for (name, _d, _s, handler) in TOOLS
    }

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return tool_defs

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        handler = name_to_handler.get(name)
        if handler is None:
            return [_err("unknown_tool", f"no such tool: {name!r}")]
        wants_keyring = name_wants_keyring.get(name, False)

        def _do_sync() -> dict[str, Any]:
            with session_factory() as session:
                if wants_keyring:
                    coro = handler(session, arguments, keyring=active_keyring)
                else:
                    coro = handler(session, arguments)
                return asyncio.run(coro)

        try:
            result = await asyncio.to_thread(_do_sync)
        except CatalogError as exc:
            return [_err(exc.code, str(exc))]
        except KeyringError as exc:
            return [_err(exc.code, str(exc))]
        except Exception as exc:
            return [_err("internal", f"{type(exc).__name__}: {exc}")]

        return [_ok(result)]


def _ok(data: dict[str, Any]) -> types.TextContent:
    return types.TextContent(
        type="text",
        text=json.dumps({"ok": True, "data": data}, default=str, indent=2),
    )


def _err(code: str, message: str) -> types.TextContent:
    return types.TextContent(
        type="text",
        text=json.dumps(
            {"ok": False, "error": {"code": code, "message": message}}, indent=2
        ),
    )
