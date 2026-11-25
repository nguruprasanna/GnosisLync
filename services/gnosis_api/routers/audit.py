"""Audit trail (read-only). Row-level security limits events to the caller's tenants; shared/platform
events are visible to shared-knowledge writers only."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from gnosis.context.db import tenant_transaction
from gnosis_api.deps import AdminCtx, EngineDep

router = APIRouter(prefix="/v1/audit-events", tags=["audit"])

_EVENTS = text("""
    SELECT seq, ts, tenant_id, actor, actor_role, action, object_ref, details
    FROM audit_event
    WHERE (CAST(:ref AS text) IS NULL OR object_ref = :ref)
    ORDER BY seq DESC
    LIMIT :limit
""")


@router.get("")
def list_audit_events(
    ctx: AdminCtx,
    eng: EngineDep,
    object_ref: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        rows = conn.execute(_EVENTS, {"ref": object_ref, "limit": limit}).mappings()
        return {"events": [dict(r) for r in rows]}
