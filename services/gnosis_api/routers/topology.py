"""Site topology (read-only for now; admin writes arrive with increment 3)."""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from gnosis.context.db import tenant_transaction
from gnosis_api.deps import Ctx, EngineDep

router = APIRouter(prefix="/v1", tags=["topology"])

_ENVIRONMENTS = text("""
    SELECT env.environment_id, env.code, env.env_class, env.runtime, t.code AS tenant_code,
           t.name AS tenant_name, s.code AS site_code, i.code AS instance_code,
           p.code AS product_code, r.code AS release_code, r.ordinal AS release_ordinal
    FROM environment env
    JOIN tenant t ON t.tenant_id = env.tenant_id
    JOIN system_instance i ON i.instance_id = env.instance_id
    JOIN site s ON s.site_id = i.site_id
    JOIN product_release r ON r.release_id = env.release_id
    JOIN product p ON p.product_id = r.product_id
    WHERE env.valid_to IS NULL
    ORDER BY t.code, s.code, i.code, env.code
""")


@router.get("/environments")
def list_environments(ctx: Ctx, eng: EngineDep) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        return {"environments": [dict(r) for r in conn.execute(_ENVIRONMENTS).mappings()]}
