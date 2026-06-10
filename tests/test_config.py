# tests/test_config.py
from ttio_mcp.config import Config


def test_from_env_defaults(monkeypatch):
    for k in ("TTIO_WB_URL", "TTIO_WB_TOKEN", "TTIO_WB_USERNAME",
              "TTIO_MCP_EXPORT_DIR", "TTIO_MCP_CACHE_DIR", "TTIO_MCP_PAGE_SIZE"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.url is None
    assert cfg.token is None
    assert cfg.page_size == 100
    assert cfg.export_dir.name == "exports"
    assert cfg.cache_dir.name == "cache"


def test_from_env_reads_values(monkeypatch, tmp_path):
    monkeypatch.setenv("TTIO_WB_URL", "wss://h:18443/transport")
    monkeypatch.setenv("TTIO_WB_TOKEN", "ttiowbk_abc")
    monkeypatch.setenv("TTIO_WB_USERNAME", "alice")
    monkeypatch.setenv("TTIO_MCP_EXPORT_DIR", str(tmp_path / "e"))
    monkeypatch.setenv("TTIO_MCP_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("TTIO_MCP_PAGE_SIZE", "250")
    cfg = Config.from_env()
    assert cfg.url == "wss://h:18443/transport"
    assert cfg.token == "ttiowbk_abc"
    assert cfg.username == "alice"
    assert cfg.page_size == 250
    assert cfg.export_dir == tmp_path / "e"
    assert cfg.cache_dir == tmp_path / "c"
