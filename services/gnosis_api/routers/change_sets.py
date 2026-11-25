"""Change sets and review decisions (docs/architecture/05 §2 Review; Studio, human sessions)."""

from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from gnosis.context.db import tenant_transaction
from gnosis.review import service
from gnosis_api.deps import AdminCtx, Ctx, EngineDep, RolesDep
from gnosis_api.problems import ApiProblem

router = APIRouter(prefix="/v1/change-sets", tags=["review"])


class DecisionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[service.DecisionInput] = Field(min_length=1, max_length=500)


@router.post("", status_code=201)
def propose(body: service.ChangeSetProposal, ctx: AdminCtx, eng: EngineDep) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        cs_id = service.propose(conn, ctx, body, produced_by=f"api:{ctx.principal.subject}")
        return _get_or_404(conn, cs_id)


@router.get("")
def list_change_sets(
    ctx: Ctx, eng: EngineDep, status: Annotated[str | None, Query(max_length=30)] = None
) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        return {"change_sets": service.list_change_sets(conn, status)}


@router.get("/{change_set_id}")
def get_change_set(change_set_id: UUID, ctx: Ctx, eng: EngineDep) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        return _get_or_404(conn, change_set_id)


@router.post("/{change_set_id}:submit")
def submit(change_set_id: UUID, ctx: AdminCtx, eng: EngineDep) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        service.submit(conn, ctx, change_set_id)
        return _get_or_404(conn, change_set_id)


@router.post("/{change_set_id}/decisions")
def decide(
    *,
    change_set_id: UUID,
    body: DecisionsRequest,
    ctx: AdminCtx,
    eng: EngineDep,
    roles: RolesDep,
    x_gnosis_role: Annotated[str, Header(min_length=1, max_length=100)],
) -> dict[str, Any]:
    with tenant_transaction(eng, ctx) as conn:
        results = service.decide(conn, ctx, change_set_id, body.decisions, role=x_gnosis_role, roles=roles)
        return {"results": [asdict(r) for r in results], "change_set": _get_or_404(conn, change_set_id)}


def _get_or_404(conn: Any, cs_id: UUID) -> dict[str, Any]:
    found = service.get_change_set(conn, cs_id)
    if found is None:
        raise ApiProblem(404, "NOT_FOUND", "Change set not found")
    return found
