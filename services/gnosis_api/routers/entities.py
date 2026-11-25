"""Knowledge exploration endpoints (docs/architecture/05 §2 Knowledge)."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import text

from gnosis.context.db import tenant_transaction
from gnosis.knowledge.registry import list_versions
from gnosis_api.deps import Ctx, EngineDep
from gnosis_api.problems import ApiProblem

router = APIRouter(prefix="/v1/entities", tags=["knowledge"])

_ENTITY_SQL = text("""
    SELECT e.entity_id, e.scope, e.kind, e.canonical_key, e.tenant_id, e.domain_codes,
           v.version_id, v.version_no, v.status, v.name, v.definition, v.verification_basis,
           v.financial_significance, v.epistemic_class
    FROM knowledge_entity e
    LEFT JOIN entity_head h ON h.entity_id = e.entity_id
    LEFT JOIN entity_version v ON v.version_id = coalesce(h.verified_version, h.working_version)
    WHERE e.entity_id = :id
""")


def _not_found() -> ApiProblem:
    # Same response whether the entity is absent or belongs to another tenant.
    return ApiProblem(404, "NOT_FOUND", "Entity not found")


_LIST_SQL = text("""
    SELECT e.entity_id, e.scope, e.kind, e.canonical_key, e.tenant_id, e.domain_codes,
           v.version_no, v.status, v.name, v.financial_significance, v.verification_basis,
           (h.verified_version IS NOT NULL) AS has_verified
    FROM knowledge_entity e
    JOIN entity_head h ON h.entity_id = e.entity_id
    JOIN entity_version v ON v.version_id = coalesce(h.verified_version, h.working_version)
    WHERE (CAST(:kind AS text) IS NULL OR e.kind = :kind)
      AND (CAST(:scope AS text) IS NULL OR e.scope = :scope)
      AND (CAST(:status AS text) IS NULL OR v.status = :status)
      AND (CAST(:q AS text) IS NULL
           OR v.search_tsv @@ plainto_tsquery('simple', :q)
           OR v.name ILIKE '%' || :q || '%'
           OR e.canonical_key ILIKE '%' || :q || '%')
    ORDER BY e.scope, e.kind, e.canonical_key
    LIMIT :limit
""")


@router.get("")
def list_entities(
    *,
    ctx: Ctx,
    eng: EngineDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    kind: Annotated[str | None, Query(max_length=100)] = None,
    scope: Literal["GENERIC", "PRODUCT", "SITE", "SVARM_INTERNAL"] | None = None,
    status: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Browse/search knowledge visible to the caller (row-level security applies).

    Phase 1 lexical search (full text + substring); hybrid vector retrieval arrives with increment 4.
    """
    params = {"q": q or None, "kind": kind, "scope": scope, "status": status, "limit": limit}
    with tenant_transaction(eng, ctx) as conn:
        rows = conn.execute(_LIST_SQL, params).mappings()
        return {"entities": [dict(r) for r in rows]}


@router.get("/{entity_id}")
def get_entity(entity_id: UUID, ctx: Ctx, eng: EngineDep) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        row = conn.execute(_ENTITY_SQL, {"id": entity_id}).mappings().first()
    if row is None:
        raise _not_found()
    return dict(row)


@router.get("/{entity_id}/versions")
def get_versions(entity_id: UUID, ctx: Ctx, eng: EngineDep) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        exists = conn.execute(text("SELECT 1 FROM knowledge_entity WHERE entity_id = :id"), {"id": entity_id})
        if exists.first() is None:
            raise _not_found()
        versions = list_versions(conn, entity_id)
    return {"entity_id": entity_id, "versions": versions}
