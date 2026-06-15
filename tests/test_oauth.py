"""Tests for KeycloakTokenVerifier (Task 2).

These tests exercise JWT validation via a locally-generated RSA key and a
_FakeJWKS that replaces the real PyJWKClient so no network is needed.
"""
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from ttio_mcp.oauth import KeycloakTokenVerifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign(priv, claims, kid="k1", alg="RS256"):
    return jwt.encode(claims, priv, algorithm=alg, headers={"kid": kid})


class _FakeJWKS:
    """Drop-in replacement for PyJWKClient that always returns the supplied key."""

    def __init__(self, priv, kid="k1"):
        self._pub = priv.public_key()
        self._kid = kid

    def get_signing_key_from_jwt(self, token):
        class _K:
            pass

        k = _K()
        k.key = self._pub
        return k


def _verifier(priv):
    v = KeycloakTokenVerifier(
        jwks_url="https://kc/realms/ttio/protocol/openid-connect/certs",
        issuer="https://kc/realms/ttio",
        audience="ttio-mcp",
        algorithms=["RS256"],
        required_scopes=["ttio.connector"],
    )
    v._jwks = _FakeJWKS(priv)  # inject fake JWKS client
    return v


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_valid_token_accepted():
    priv = _key()
    now = int(time.time())
    tok = _sign(
        priv,
        {
            "iss": "https://kc/realms/ttio",
            "aud": "ttio-mcp",
            "sub": "01HACCT",
            "preferred_username": "alice",
            "scope": "ttio.connector",
            "exp": now + 300,
        },
    )
    at = await _verifier(priv).verify_token(tok)
    assert at is not None
    assert at.subject == "01HACCT"
    assert "ttio.connector" in at.scopes
    assert at.token == tok


async def test_wrong_audience_rejected():
    priv = _key()
    now = int(time.time())
    tok = _sign(
        priv,
        {
            "iss": "https://kc/realms/ttio",
            "aud": "tti-workbench",
            "sub": "x",
            "exp": now + 300,
        },
    )
    assert await _verifier(priv).verify_token(tok) is None


async def test_expired_rejected():
    priv = _key()
    now = int(time.time())
    tok = _sign(
        priv,
        {
            "iss": "https://kc/realms/ttio",
            "aud": "ttio-mcp",
            "sub": "x",
            "exp": now - 300,  # well beyond the 30s leeway
        },
    )
    assert await _verifier(priv).verify_token(tok) is None


async def test_wrong_issuer_rejected():
    priv = _key()
    now = int(time.time())
    tok = _sign(
        priv,
        {
            "iss": "https://evil/realms/x",
            "aud": "ttio-mcp",
            "sub": "x",
            "exp": now + 300,
        },
    )
    assert await _verifier(priv).verify_token(tok) is None


async def test_bad_signature_rejected():
    priv = _key()
    other = _key()
    now = int(time.time())
    # Token signed by `other`, but verifier holds `priv`'s public key
    tok = _sign(
        other,
        {
            "iss": "https://kc/realms/ttio",
            "aud": "ttio-mcp",
            "sub": "x",
            "exp": now + 300,
        },
    )
    assert await _verifier(priv).verify_token(tok) is None


async def test_missing_required_scope_rejected():
    """Token with no matching required scope must be rejected."""
    priv = _key()
    now = int(time.time())
    tok = _sign(
        priv,
        {
            "iss": "https://kc/realms/ttio",
            "aud": "ttio-mcp",
            "sub": "x",
            "scope": "profile email",  # missing ttio.connector
            "exp": now + 300,
        },
    )
    assert await _verifier(priv).verify_token(tok) is None


# ---------------------------------------------------------------------------
# Task 3: exchange_for_workbench (RFC 8693 token exchange)
# ---------------------------------------------------------------------------

import httpx

from ttio_mcp.oauth import exchange_for_workbench


async def test_exchange_posts_and_returns_token(monkeypatch):
    """exchange_for_workbench POSTs RFC 8693 form data and returns (token, expires_at)."""
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"access_token": "wb.jwt.token", "expires_in": 300}

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **k):
            captured["url"] = url
            captured["data"] = data
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    tok, exp = await exchange_for_workbench(
        user_token="user.jwt",
        token_url="https://kc/realms/ttio/protocol/openid-connect/token",
        client_id="ttio-mcp",
        client_secret="s3cr3t",
        audience="tti-workbench",
    )
    assert tok == "wb.jwt.token"
    assert exp > 0
    assert captured["url"] == "https://kc/realms/ttio/protocol/openid-connect/token"
    assert captured["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert captured["data"]["subject_token"] == "user.jwt"
    assert captured["data"]["audience"] == "tti-workbench"
    assert captured["data"]["subject_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
    assert captured["data"]["client_id"] == "ttio-mcp"
    assert captured["data"]["client_secret"] == "s3cr3t"


async def test_exchange_no_expires_in_returns_zero(monkeypatch):
    """When the response omits expires_in, expires_at should be 0 (never-expires sentinel)."""
    class _Resp:
        status_code = 200

        def json(self):
            return {"access_token": "wb.jwt.no-expiry"}

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    tok, exp = await exchange_for_workbench(
        user_token="user.jwt",
        token_url="https://kc/token",
        client_id="ttio-mcp",
        client_secret="s3cr3t",
        audience="tti-workbench",
    )
    assert tok == "wb.jwt.no-expiry"
    assert exp == 0


async def test_exchange_http_error_propagates(monkeypatch):
    """A non-200 response (raise_for_status raises) should propagate as an exception."""

    class _Resp:
        status_code = 401

        def json(self):
            return {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "401 Unauthorized",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    import pytest

    with pytest.raises(httpx.HTTPStatusError):
        await exchange_for_workbench(
            user_token="bad.jwt",
            token_url="https://kc/token",
            client_id="ttio-mcp",
            client_secret="s3cr3t",
            audience="tti-workbench",
        )
