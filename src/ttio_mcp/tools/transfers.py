# src/ttio_mcp/tools/transfers.py
"""Upload/download tools with a single mode selector; plus federation peers."""
from __future__ import annotations

import base64

from mcp.server.fastmcp import FastMCP
from ttio.workbench.client import ServerRecipient

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError, to_tool_error
from ttio_mcp.tools._serialize import ser as _ser

_MODES = {"plain", "byok", "server-kek", "pqc"}


def _decode_key(s: str) -> bytes:
    """Accept hex (64 chars) or base64 for a 32-byte key."""
    if len(s) == 64:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    return base64.b64decode(s)


def _result(res) -> dict:
    return {
        "container_uri": getattr(res, "container_uri", None),
        "last_acked_au_sequence": getattr(res, "last_acked_au_sequence", None),
        "resume_handle": getattr(res, "resume_handle", None),
    }


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    async def ttio_upload(project: str, container_uri: str, path: str, mode: str = "plain",
                          key: str | None = None, kek_id: str | None = None,
                          recipient_public_key: str | None = None,
                          encrypt_headers: bool = False, preview: bool = False) -> dict:
        """Upload a local .tio to the server.

        mode=plain         : no encryption.
        mode=byok          : caller key (hex/base64, 32 bytes), AES-256-GCM per-AU.
        mode=server-kek    : multi-recipient with a server ServerRecipient(kek_id) (HSM-wrapped).
        mode=pqc           : ML-KEM-1024 recipient_public_key (preview-gated; pass preview=true).
        """
        if mode not in _MODES:
            return {"error": f"mode must be one of {sorted(_MODES)}"}
        try:
            client = conn.require_client()
            if mode == "plain":
                res = await client.upload_path(project=project, container_uri=container_uri, path=path)
            elif mode == "byok":
                if not key:
                    raise ToolError("byok upload requires key=")
                res = await client.upload_encrypted(project=project, container_uri=container_uri,
                                                    tio_path=path, key=_decode_key(key),
                                                    encrypt_headers=encrypt_headers)
            elif mode == "server-kek":
                if not kek_id:
                    raise ToolError("server-kek upload requires kek_id=")
                rec = ServerRecipient(recipient_id="", kek_id=kek_id)
                res = await client.upload_encrypted_multi(project=project, container_uri=container_uri,
                                                          tio_path=path, recipients=[rec],
                                                          encrypt_headers=encrypt_headers)
            else:  # pqc
                if not recipient_public_key:
                    raise ToolError("pqc upload requires recipient_public_key=")
                res = await client.upload_encrypted_pqc(project=project, container_uri=container_uri,
                                                        tio_path=path,
                                                        recipient_public_key=_decode_key(recipient_public_key),
                                                        preview=preview)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return _result(res)

    @app.tool()
    async def ttio_download(container_uri: str, out_path: str, mode: str = "plain",
                            key: str | None = None, recipient_private_key: str | None = None,
                            filters: dict | None = None, max_au: int = 0, preview: bool = False) -> dict:
        """Download a container to a local file.

        mode=plain      : write raw .tis bytes to out_path.
        mode=byok       : caller key (hex/base64) decrypts per-AU; out_path is a plaintext .tio.
        mode=server-kek : download_via_server — server unwraps the DEK via HSM; out_path left decrypted.
        mode=pqc        : ML-KEM-1024 recipient_private_key decrypts (preview-gated).
        filters: selective-access dict (ms_level, polarity, retention_time_min/max,
                 precursor_mz_min/max, precursor_charge).
        """
        if mode not in _MODES:
            return {"error": f"mode must be one of {sorted(_MODES)}"}
        try:
            client = conn.require_client()
            if mode == "plain":
                res = await client.download_bytes(container_uri=container_uri, filters=filters,
                                                  output_mode="binary", max_au=max_au)
                with open(out_path, "wb") as fh:
                    fh.write(getattr(res, "payload", b"") or b"")
                return {"out_path": out_path, "bytes": len(getattr(res, "payload", b"") or b"")}
            if mode == "byok":
                if not key:
                    raise ToolError("byok download requires key=")
                await client.download_decrypted(container_uri=container_uri, key=_decode_key(key),
                                                out_tio_path=out_path, filters=filters, max_au=max_au)
                return {"out_path": out_path}
            if mode == "server-kek":
                meta = await client.download_via_server(container_uri=container_uri,
                                                        out_tio_path=out_path, filters=filters, max_au=max_au)
                return {"out_path": out_path, "runs": sorted(meta.keys()) if isinstance(meta, dict) else None}
            # pqc
            if not recipient_private_key:
                raise ToolError("pqc download requires recipient_private_key=")
            await client.download_decrypted_pqc(container_uri=container_uri,
                                                recipient_private_key=_decode_key(recipient_private_key),
                                                out_tio_path=out_path, preview=preview)
            return {"out_path": out_path}
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_federation_peers() -> dict:
        """List federation peers (empty on single-node v1.0)."""
        try:
            peers = conn.require_client().federation().peers()
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"peers": [_ser(p) for p in peers]}
