"""OAuth / Keycloak integration for ttio-mcp.

Responsibilities (one module, no FastMCP/connection imports):
  - KeycloakTokenVerifier: validates a Keycloak JWT (JWKS, aud, iss, algs,
    required scopes) and returns an mcp AccessToken or None on any failure.
  - exchange_for_workbench: RFC 8693 token-exchange to obtain a
    tti-workbench-audience token from Keycloak.
"""
from __future__ import annotations

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier


class KeycloakTokenVerifier(TokenVerifier):
    """Validate a Keycloak access token (aud=ttio-mcp) via JWKS.

    Returns an mcp AccessToken on success, None on any failure (-> 401).
    Never raises — the SDK treats None as a 401 response.
    """

    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str,
        audience: str,
        algorithms: list[str],
        required_scopes: list[str] | None = None,
    ) -> None:
        # Stored as self._jwks so tests can inject a _FakeJWKS instance.
        self._jwks = PyJWKClient(jwks_url)  # fetches + caches JWKS from Keycloak
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._required_scopes = required_scopes or []

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate *token* and return an AccessToken, or None on any failure."""
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
                leeway=30,  # tolerate up to 30s clock drift (matches the workbench)
            )
        except Exception:
            # Any decode/validation error (expired, wrong aud/iss, bad sig,
            # missing required claim, network error, etc.) → 401.
            return None

        # Enforce required scopes (scope claim is space-separated per RFC 8693,
        # though some IdPs emit a list). A malformed scope claim type → 401.
        raw_scope = claims.get("scope", "")
        if isinstance(raw_scope, str):
            scope_list: list[str] = raw_scope.split()
        elif isinstance(raw_scope, (list, tuple)):
            scope_list = [str(s) for s in raw_scope]
        else:
            return None
        if any(s not in scope_list for s in self._required_scopes):
            return None

        return AccessToken(
            token=token,
            # azp (authorized party) is the Keycloak client_id field for access tokens.
            client_id=claims.get("azp") or claims.get("client_id") or self._audience,
            scopes=scope_list,
            expires_at=claims.get("exp"),
            subject=claims.get("sub"),
            claims=claims,
        )
