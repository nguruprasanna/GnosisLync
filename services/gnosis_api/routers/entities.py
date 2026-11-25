"""Knowledge exploration endpoints (docs/architecture/05 §2 Knowledge)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter
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
