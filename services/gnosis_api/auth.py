"""Token verification → Principal.

Phase 0 supports HS256 tokens for development and tests only. Phase 1 adds OIDC (JWKS) verification
against the Svarm IdP behind the same ``TokenVerifier`` protocol.

Expected claims::

    sub      principal id
    aud/iss  must match settings
    exp      required
    tenants  {"<TENANT_CODE>": "<tenant uuid>", ...}   entitlements
    gnosis_shared_write  bool (optional)
    roles    ["<role profile id>", ...] (optional) role profiles the principal may act under
    principal_type  "HUMAN" | "SERVICE" (optional, default SERVICE). Review decisions require HUMAN.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import jwt

from gnosis.context import ContextError, Principal, PrincipalKind


class TokenVerifier(Protocol):
    def verify(self, token: str) -> tuple[Principal, dict[str, UUID]]: ...


class HS256DevVerifier:
    def __init__(self, secret: str, audience: str, issuer: str) -> None:
        if len(secret) < 32:
            raise ValueError("HS256 secret must be at least 32 characters")
        self._secret = secret
        self._audience = audience
        self._issuer = issuer

    def verify(self, token: str) -> tuple[Principal, dict[str, UUID]]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise ContextError("INVALID_TOKEN", str(exc)) from exc
        raw = claims.get("tenants") or {}
        if not isinstance(raw, dict):
            raise ContextError("INVALID_TOKEN", "tenants claim must be an object")
        try:
            tenants = {str(code): UUID(str(tid)) for code, tid in raw.items()}
        except ValueError as exc:
            raise ContextError("INVALID_TOKEN", "tenants claim contains an invalid id") from exc
        roles = claims.get("roles") or []
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise ContextError("INVALID_TOKEN", "roles claim must be a list of strings")
        try:
            kind = PrincipalKind(claims.get("principal_type", PrincipalKind.SERVICE.value))
        except ValueError as exc:
            raise ContextError("INVALID_TOKEN", "principal_type must be HUMAN or SERVICE") from exc
        principal = Principal(
            subject=str(claims["sub"]),
            tenant_ids=frozenset(tenants.values()),
            can_write_shared=claims.get("gnosis_shared_write") is True,
            roles=frozenset(roles),
            kind=kind,
        )
        return principal, tenants
