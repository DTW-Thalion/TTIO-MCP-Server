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


def test_transport_defaults_to_stdio(monkeypatch):
    for k in ("TTIO_MCP_TRANSPORT", "TTIO_MCP_HTTP_HOST", "TTIO_MCP_HTTP_PORT", "TTIO_MCP_HTTP_PATH"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.transport == "stdio"
    assert cfg.http_host == "127.0.0.1"
    assert cfg.http_port == 8000
    assert cfg.http_path == "/mcp"


def test_http_transport_from_env(monkeypatch):
    monkeypatch.setenv("TTIO_MCP_TRANSPORT", "http")
    monkeypatch.setenv("TTIO_MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("TTIO_MCP_HTTP_PORT", "9001")
    monkeypatch.setenv("TTIO_MCP_HTTP_PATH", "/ttio")
    cfg = Config.from_env()
    assert (cfg.transport, cfg.http_host, cfg.http_port, cfg.http_path) == ("http", "0.0.0.0", 9001, "/ttio")


def test_transport_is_validated(monkeypatch):
    import pytest
    monkeypatch.setenv("TTIO_MCP_TRANSPORT", "carrier-pigeon")
    with pytest.raises(ValueError):
        Config.from_env()


def test_oauth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TTIO_MCP_OAUTH_ISSUER", raising=False)
    cfg = Config.from_env()
    assert cfg.oauth_enabled is False
    assert cfg.oauth_issuer is None


def test_oauth_enabled_from_env(monkeypatch):
    monkeypatch.setenv("TTIO_MCP_OAUTH_ISSUER", "https://kc.example/realms/ttio")
    monkeypatch.setenv("TTIO_MCP_OAUTH_RESOURCE_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("TTIO_MCP_OAUTH_JWKS_URL", "https://kc.example/realms/ttio/protocol/openid-connect/certs")
    monkeypatch.setenv("TTIO_MCP_OAUTH_TOKEN_URL", "https://kc.example/realms/ttio/protocol/openid-connect/token")
    monkeypatch.setenv("TTIO_MCP_OAUTH_CLIENT_ID", "ttio-mcp")
    monkeypatch.setenv("TTIO_MCP_OAUTH_CLIENT_SECRET", "s3cr3t")
    cfg = Config.from_env()
    assert cfg.oauth_enabled is True
    assert cfg.oauth_audience == "ttio-mcp"               # default
    assert cfg.oauth_exchange_audience == "tti-workbench"  # default
    assert cfg.oauth_required_scopes == ("ttio.connector",)  # default (tuple)
    assert cfg.oauth_client_id == "ttio-mcp"
    assert cfg.oauth_client_secret == "s3cr3t"
    assert cfg.oauth_issuer == "https://kc.example/realms/ttio"
    assert cfg.oauth_resource_url == "https://mcp.example/mcp"
    assert cfg.oauth_jwks_url == "https://kc.example/realms/ttio/protocol/openid-connect/certs"
    assert cfg.oauth_token_url == "https://kc.example/realms/ttio/protocol/openid-connect/token"


def test_oauth_allowed_algs_default(monkeypatch):
    monkeypatch.delenv("TTIO_MCP_OAUTH_ISSUER", raising=False)
    cfg = Config.from_env()
    assert cfg.oauth_allowed_algs == ("RS256",)


def test_oauth_issuer_only_is_valid(monkeypatch):
    # A partial config (issuer set, JWKS/token unset) is valid and must not raise.
    for k in ("TTIO_MCP_OAUTH_JWKS_URL", "TTIO_MCP_OAUTH_TOKEN_URL",
              "TTIO_MCP_OAUTH_CLIENT_ID", "TTIO_MCP_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TTIO_MCP_OAUTH_ISSUER", "https://kc.example/realms/ttio")
    cfg = Config.from_env()
    assert cfg.oauth_enabled is True
    assert cfg.oauth_jwks_url is None
    assert cfg.oauth_allowed_algs == ("RS256",)
