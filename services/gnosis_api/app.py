"""FastAPI application factory.

No `from __future__ import annotations` here: FastAPI must resolve locally defined Annotated aliases.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text

from gnosis.config import Settings
from gnosis.context import ContextError, Purpose, RequestContext
from gnosis.context.db import make_engine, tenant_transaction
from gnosis_api.auth import HS256DevVerifier, TokenVerifier

PROBLEM = "application/problem+json"


def _problem(status: int, code: str, title: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM,
        content={
            "type": f"https://gnosislync.svarm/problems/{code.lower()}",
            "title": title,
            "status": status,
            "gnosis_code": code,
        },
    )


def create_app(
    settings: Settings | None = None, engine: Engine | None = None, verifier: TokenVerifier | None = None
) -> FastAPI:
    settings = settings or Settings()
    engine = engine or make_engine(settings.database_url)
    if verifier is None and settings.jwt_hs256_secret is not None:
        verifier = HS256DevVerifier(
            settings.jwt_hs256_secret.get_secret_value(), settings.jwt_audience, settings.jwt_issuer
        )

    app = FastAPI(title="GnosisLync Intelligence API", version="0.0.1")

    @app.exception_handler(ContextError)
    async def _context_error(_: Request, exc: ContextError) -> JSONResponse:
        status = 401 if exc.code == "INVALID_TOKEN" else 403
        return _problem(status, exc.code, "Request context rejected")

    def request_context(
        authorization: Annotated[str | None, Header()] = None,
        x_gnosis_tenant: Annotated[str | None, Header()] = None,
        x_gnosis_purpose: Annotated[str | None, Header()] = None,
    ) -> RequestContext:
        if verifier is None:
            raise ContextError("INVALID_TOKEN", "token verification is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise ContextError("INVALID_TOKEN", "missing bearer token")
        principal, tenant_codes = verifier.verify(authorization.removeprefix("Bearer ").strip())
        try:
            purpose = Purpose(x_gnosis_purpose or "")
        except ValueError:
            raise HTTPException(
                400, "X-Gnosis-Purpose header is required and must be a known purpose"
            ) from None
        requested: UUID | None = None
        if x_gnosis_tenant is not None:
            if x_gnosis_tenant not in tenant_codes:
                raise ContextError("TENANT_FORBIDDEN", "principal is not entitled to the requested tenant")
            requested = tenant_codes[x_gnosis_tenant]
        return RequestContext.build(principal, requested, purpose)

    Ctx = Annotated[RequestContext, Depends(request_context)]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/entities/{entity_id}", response_model=None)
    def get_entity(entity_id: UUID, ctx: Ctx) -> dict[str, Any] | JSONResponse:
        with tenant_transaction(engine, ctx) as conn:
            row = (
                conn.execute(
                    text("""
                    SELECT e.entity_id, e.scope, e.kind, e.canonical_key, e.tenant_id, e.domain_codes,
                           v.version_no, v.status, v.name, v.definition, v.verification_basis,
                           v.financial_significance, v.epistemic_class
                    FROM knowledge_entity e
                    LEFT JOIN entity_head h ON h.entity_id = e.entity_id
                    LEFT JOIN entity_version v
                           ON v.version_id = coalesce(h.verified_version, h.working_version)
                    WHERE e.entity_id = :id
                """),
                    {"id": entity_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            # Same response whether the entity is absent or belongs to another tenant.
            return _problem(404, "NOT_FOUND", "Entity not found")
        result = dict(row)
        for key in ("entity_id", "tenant_id"):
            if result[key] is not None:
                result[key] = str(result[key])
        return result

    return app
