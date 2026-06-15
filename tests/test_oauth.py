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
