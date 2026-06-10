# tests/test_tools_transfers.py
import asyncio
import base64

import pytest
from mcp.server.fastmcp import FastMCP

from tests.conftest import FakeWorkbenchClient
from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import transfers as tr


class _Client(FakeWorkbenchClient):
    def __init__(self):
        super().__init__()
        self.recorded = {}

    async def upload_path(self, *, project, container_uri, path, resume=None, progress=None, chunk_size=None):
        self.recorded = dict(mode="plain", project=project, uri=container_uri, path=path)
        return type("R", (), {"container_uri": container_uri, "last_acked_au_sequence": 3, "resume_handle": None})()

    async def upload_encrypted_multi(self, *, project, container_uri, tio_path, recipients,
                                     server_kek_id=None, encrypt_headers=False, resume=None, preview=False):
        self.recorded = dict(mode="server-kek", kek=recipients[0].kek_id, uri=container_uri)
        return type("R", (), {"container_uri": container_uri, "last_acked_au_sequence": 3, "resume_handle": None})()

    async def download_via_server(self, *, container_uri, out_tio_path, filters=None, max_au=0):
        self.recorded = dict(mode="server-kek-dl", uri=container_uri, out=out_tio_path)
        return {"run_0001": {"mz": [1, 2, 3]}}


def _app():
    cm = ConnectionManager()
    fc = _Client()
    fc.set_subclient("federation", type("F", (), {"peers": lambda self: []})())
    cm._inject(fc)
    app = FastMCP("t")
    tr.register(app, cm, Config.from_env())
    return app, fc


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_upload_plain(tmp_path):
    f = tmp_path / "x.tio"
    f.write_bytes(b"data")
    app, fc = _app()
    out = _call(app, "ttio_upload", mode="plain", project="adni",
                container_uri="uri:tio:1", path=str(f))
    assert fc.recorded["mode"] == "plain"
    assert out["last_acked_au_sequence"] == 3


def test_upload_server_kek(tmp_path):
    f = tmp_path / "x.tio"
    f.write_bytes(b"data")
    app, fc = _app()
    _call(app, "ttio_upload", mode="server-kek", project="adni",
                container_uri="uri:tio:1", path=str(f), kek_id="server:rewrap-v1")
    assert fc.recorded["mode"] == "server-kek"
    assert fc.recorded["kek"] == "server:rewrap-v1"


def test_download_server_kek(tmp_path):
    out_path = tmp_path / "out.tio"
    app, fc = _app()
    out = _call(app, "ttio_download", mode="server-kek",
                container_uri="uri:tio:1", out_path=str(out_path))
    assert fc.recorded["mode"] == "server-kek-dl"
    assert out["out_path"] == str(out_path)


def test_federation_peers():
    app, _ = _app()
    out = _call(app, "ttio_federation_peers")
    assert out["peers"] == []


def test_decode_key_rejects_garbage():
    from ttio_mcp.errors import ToolError
    from ttio_mcp.tools.transfers import _decode_key
    with pytest.raises(ToolError):
        _decode_key("not a valid key!!!")


def test_decode_key_accepts_hex_and_base64():
    from ttio_mcp.tools.transfers import _decode_key
    k = b"0" * 32
    assert len(_decode_key(k.hex())) == 32
    assert len(_decode_key(base64.b64encode(k).decode())) == 32
