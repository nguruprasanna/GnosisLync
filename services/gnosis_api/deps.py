"""Shared request dependencies. Application state is set up by ``create_app``.

No `from __future__ import annotations`: FastAPI must resolve the Annotated aliases at import time.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import Engine

from gnosis.context import ContextError, Purpose, RequestContext
from gnosis.roles import RoleRegistry
from gnosis_api.auth import TokenVerifier
from gnosis_api.problems import ApiProblem


def request_context(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_gnosis_tenant: Annotated[str | None, Header()] = None,
    x_gnosis_purpose: Annotated[str | None, Header()] = None,
) -> RequestContext:
    verifier: TokenVerifier | None = request.app.state.verifier
    if verifier is None:
        raise ContextError("INVALID_TOKEN", "token verification is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise ContextError("INVALID_TOKEN", "missing bearer token")
    principal, tenant_codes = verifier.verify(authorization.removeprefix("Bearer ").strip())
    try:
        purpose = Purpose(x_gnosis_purpose or "")
    except ValueError:
        raise ApiProblem(
            400,
            "PURPOSE_REQUIRED",
            "Purpose required",
            f"X-Gnosis-Purpose must be one of: {', '.join(p.value for p in Purpose)}",
        ) from None
    requested: UUID | None = None
    if x_gnosis_tenant is not None:
        if x_gnosis_tenant not in tenant_codes:
            raise ContextError("TENANT_FORBIDDEN", "principal is not entitled to the requested tenant")
        requested = tenant_codes[x_gnosis_tenant]
    return RequestContext.build(principal, requested, purpose)


Ctx = Annotated[RequestContext, Depends(request_context)]


def admin_context(ctx: Ctx) -> RequestContext:
    """Knowledge changes and reviews are only allowed under purpose KNOWLEDGE_ADMIN."""
    if ctx.purpose is not Purpose.KNOWLEDGE_ADMIN:
        raise ApiProblem(403, "PURPOSE_NOT_PERMITTED", "Purpose not permitted", "requires KNOWLEDGE_ADMIN")
    return ctx


AdminCtx = Annotated[RequestContext, Depends(admin_context)]


def engine(request: Request) -> Engine:
    eng: Engine = request.app.state.engine
    return eng


def roles(request: Request) -> RoleRegistry:
    reg: RoleRegistry = request.app.state.roles
    return reg


EngineDep = Annotated[Engine, Depends(engine)]
RolesDep = Annotated[RoleRegistry, Depends(roles)]
