"""Change sets and review decisions (docs/architecture/02 §4, 04 §4.4 ``review_knowledge_change``, 07 §3.5).

This is the only module that sets ``entity_head.verified_version``. Rules enforced here, in addition to the
lifecycle invariants (gnosis.knowledge.lifecycle, mirrored by a DB trigger):

* review decisions only from HUMAN principals acting under a granted role with review authority covering the
  item's domains, kind and financial significance;
* the author of a change set cannot review it; one decision per reviewer per item;
* HIGH significance needs two distinct qualified approvers (four-eyes);
* bulk approval (several items in one request) is refused when any approved item is FINANCIAL;
* a version only becomes VERIFIED if the entity's verified head still equals the base it was proposed
  against (``BASE_VERSION_CHANGED`` otherwise);
* every proposal, submission and decision writes an audit event in the same transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from gnosis import audit
from gnosis.context import PrincipalKind, RequestContext
from gnosis.knowledge.lifecycle import (
    DETERMINISTIC_BASES,
    Approval,
    Basis,
    Decision,
    ReviewAuthority,
    Significance,
    Status,
    VersionState,
    check_transition,
    required_approvals,
)
from gnosis.knowledge.registry import (
    KnowledgeError,
    Scope,
    VersionContent,
    add_version,
    create_entity,
    load_entity,
    parse_significance,
)
from gnosis.roles import RoleRegistry

Materiality = Literal["COSMETIC", "DESCRIPTIVE", "SEMANTIC", "FINANCIAL"]
MATERIALITY_ORDER: dict[str, int] = {"COSMETIC": 0, "DESCRIPTIVE": 1, "SEMANTIC": 2, "FINANCIAL": 3}
APPROVED = frozenset({Status.VERIFIED, Status.PROVISIONAL})


class ReviewError(KnowledgeError):
    pass


# ----------------------------------------------------------------------------------------------- inputs
class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreateItem(_Input):
    op: Literal["CREATE"]
    scope: Scope
    kind: str = Field(min_length=1, max_length=100)
    canonical_key: str = Field(min_length=1, max_length=500)
    environment_id: UUID | None = None
    product_id: UUID | None = None
    domain_codes: frozenset[str] = frozenset()
    materiality: Materiality
    version: VersionContent


class UpdateItem(_Input):
    op: Literal["UPDATE"]
    entity_id: UUID
    base_version_id: UUID
    materiality: Materiality
    version: VersionContent


ProposedItem = Annotated[CreateItem | UpdateItem, Field(discriminator="op")]


class ChangeSetProposal(_Input):
    title: str = Field(min_length=1, max_length=300)
    origin: str = Field(default="MANUAL", min_length=1, max_length=100)
    target: Literal["TENANT", "SHARED"]
    items: list[ProposedItem] = Field(min_length=1, max_length=500)


class DecisionInput(_Input):
    change_item_id: UUID
    decision: Decision
    rationale: str = Field(min_length=1, max_length=5000)


@dataclass(frozen=True)
class DecisionResult:
    change_item_id: UUID
    review_decision_id: UUID
    status: Status
    approvals_present: int
    approvals_required: int


# ----------------------------------------------------------------------------------------------- propose
def _target_tenant(ctx: RequestContext, target: str) -> UUID | None:
    if target == "SHARED":
        if not ctx.can_write_shared:
            raise ReviewError("SHARED_WRITE_FORBIDDEN", "principal may not change shared knowledge")
        return None
    if len(ctx.tenant_ids) != 1:
        raise ReviewError("TENANT_REQUIRED", "X-Gnosis-Tenant must select exactly one tenant")
    return next(iter(ctx.tenant_ids))


def _version_diff(conn: Connection, base_version_id: UUID, content: VersionContent) -> dict[str, Any]:
    row = conn.execute(
        text(
            "SELECT name, definition, attributes, release_from, release_to, epistemic_class, "
            "verification_basis, financial_significance, confidence, sensitivity "
            "FROM entity_version WHERE version_id = :v"
        ),
        {"v": base_version_id},
    ).mappings().one()  # fmt: skip
    new = content.canonical()
    changed = sorted(k for k, old in row.items() if new.get(k) != old)
    return {"base_version_id": str(base_version_id), "changed_fields": changed}


def propose(conn: Connection, ctx: RequestContext, proposal: ChangeSetProposal, *, produced_by: str) -> UUID:
    tenant_id = _target_tenant(ctx, proposal.target)
    risk = max((i.materiality for i in proposal.items), key=MATERIALITY_ORDER.__getitem__)
    cs_id: UUID = conn.execute(
        text(
            "INSERT INTO change_set (tenant_id, origin, title, risk_level, created_by) "
            "VALUES (:t, :o, :title, :risk, :by) RETURNING change_set_id"
        ),
        {
            "t": tenant_id,
            "o": proposal.origin,
            "title": proposal.title,
            "risk": risk,
            "by": ctx.principal.principal_id,
        },
    ).scalar_one()

    for item in proposal.items:
        if isinstance(item, CreateItem):
            if (item.scope == "SITE") != (tenant_id is not None and item.environment_id is not None):
                raise ReviewError(
                    "SCOPE_TARGET_MISMATCH",
                    "SITE items need target TENANT and an environment_id (and vice versa)",
                )
            if item.scope == "GENERIC" and tenant_id is not None:
                raise ReviewError(
                    "SCOPE_TARGET_MISMATCH", "GENERIC knowledge must be proposed with target SHARED"
                )
            entity_id = create_entity(
                conn,
                tenant_id=tenant_id,
                scope=item.scope,
                kind=item.kind,
                canonical_key=item.canonical_key,
                environment_id=item.environment_id,
                product_id=item.product_id,
                domain_codes=item.domain_codes,
            )
            entity = load_entity(conn, entity_id)
            base: UUID | None = None
            diff: dict[str, Any] = {}
        else:
            entity = load_entity(conn, item.entity_id, for_update=True)
            if entity is None or entity.tenant_id != tenant_id:
                raise ReviewError("NOT_FOUND", "entity not found in the change set's target")
            if entity.current_version != item.base_version_id:
                raise ReviewError("BASE_VERSION_CHANGED", "the entity changed since base_version_id")
            base = item.base_version_id
            diff = _version_diff(conn, base, item.version)
        assert entity is not None  # noqa: S101 - created or loaded above
        version_id = add_version(
            conn,
            entity,
            item.version,
            produced_by=produced_by,
            change_set_id=cs_id,
            created_by=ctx.principal.principal_id,
        )
        conn.execute(
            text(
                "INSERT INTO change_item (change_set_id, tenant_id, op, entity_version_id, base_version_id, "
                "diff, materiality) VALUES (:cs, :t, :op, :v, :base, CAST(:diff AS jsonb), :m)"
            ),
            {
                "cs": cs_id,
                "t": tenant_id,
                "op": item.op,
                "v": version_id,
                "base": base,
                "diff": json.dumps(diff),
                "m": item.materiality,
            },
        )
    audit.record(
        conn,
        ctx,
        "change_set.proposed",
        tenant_id=tenant_id,
        object_ref=f"change_set:{cs_id}",
        details={"items": len(proposal.items), "risk_level": risk, "origin": proposal.origin},
    )
    return cs_id


# ----------------------------------------------------------------------------------------------- shared
@dataclass(frozen=True)
class _ItemRow:
    change_item_id: UUID
    op: str
    materiality: str
    base_version_id: UUID | None
    version_id: UUID
    entity_id: UUID
    status: Status
    basis: Basis | None
    significance: Significance
    content_hash: bytes


_ITEMS_SQL = """
    SELECT ci.change_item_id, ci.op, ci.materiality, ci.base_version_id, v.version_id, v.entity_id, v.status,
           v.verification_basis, v.financial_significance, v.content_hash
    FROM change_item ci JOIN entity_version v ON v.version_id = ci.entity_version_id
    WHERE ci.change_set_id = :cs
"""


def _items(conn: Connection, cs_id: UUID, *, for_update: bool = False) -> list[_ItemRow]:
    sql = _ITEMS_SQL + (
        " ORDER BY ci.change_item_id FOR UPDATE OF v" if for_update else " ORDER BY ci.change_item_id"
    )
    return [
        _ItemRow(
            change_item_id=r.change_item_id,
            op=r.op,
            materiality=r.materiality,
            base_version_id=r.base_version_id,
            version_id=r.version_id,
            entity_id=r.entity_id,
            status=Status(r.status),
            basis=Basis(r.verification_basis) if r.verification_basis else None,
            significance=parse_significance(r.financial_significance),
            content_hash=bytes(r.content_hash),
        )
        for r in conn.execute(text(sql), {"cs": cs_id})
    ]


@dataclass(frozen=True)
class _ChangeSet:
    change_set_id: UUID
    tenant_id: UUID | None
    status: str
    created_by: UUID | None


def _lock_change_set(conn: Connection, cs_id: UUID) -> _ChangeSet:
    row = conn.execute(
        text(
            "SELECT change_set_id, tenant_id, status, created_by FROM change_set "
            "WHERE change_set_id = :cs FOR UPDATE"
        ),
        {"cs": cs_id},
    ).first()
    if row is None:
        # Shared rows are readable by everyone but lockable (writable) only with shared-write rights.
        visible = conn.execute(text("SELECT 1 FROM change_set WHERE change_set_id = :cs"), {"cs": cs_id})
        if visible.first():
            raise ReviewError("SHARED_WRITE_FORBIDDEN", "principal may not change shared knowledge")
        raise ReviewError("NOT_FOUND", "change set not found")
    return _ChangeSet(row.change_set_id, row.tenant_id, row.status, row.created_by)


def _set_status(
    conn: Connection, version_id: UUID, status: Status, basis: Basis | None, decision_id: UUID | None
) -> None:
    conn.execute(
        text(
            "UPDATE entity_version SET status = :s, verification_basis = :b, review_decision_id = :d "
            "WHERE version_id = :v"
        ),
        {"s": status.value, "b": basis.value if basis else None, "d": decision_id, "v": version_id},
    )


def _promote(conn: Connection, item: _ItemRow, basis: Basis | None, decision_id: UUID | None) -> None:
    """Make ``item``'s version VERIFIED and the entity's verified head, superseding the previous one."""
    head = load_entity(conn, item.entity_id, for_update=True)
    assert head is not None  # noqa: S101 - the version references it
    if head.verified_version is not None and head.verified_version != item.base_version_id:
        raise ReviewError(
            "BASE_VERSION_CHANGED", "another version was verified since this change was proposed"
        )
    _set_status(conn, item.version_id, Status.VERIFIED, basis, decision_id)
    if head.verified_version is not None:
        conn.execute(
            text("UPDATE entity_version SET status = 'SUPERSEDED' WHERE version_id = :v"),
            {"v": head.verified_version},
        )
    conn.execute(
        text("UPDATE entity_head SET verified_version = :v WHERE entity_id = :e"),
        {"v": item.version_id, "e": item.entity_id},
    )


def _refresh_change_set_status(conn: Connection, cs_id: UUID) -> str:
    statuses = [i.status for i in _items(conn, cs_id)]
    if any(s in {Status.UNDER_REVIEW, Status.DRAFT, Status.OBSERVED} for s in statuses):
        new = "IN_REVIEW"
    elif all(s in APPROVED for s in statuses):
        new = "APPROVED"
    elif not any(s in APPROVED for s in statuses):
        new = "REJECTED"
    else:
        new = "PARTIALLY_APPROVED"
    conn.execute(text("UPDATE change_set SET status = :s WHERE change_set_id = :cs"), {"s": new, "cs": cs_id})
    return new


# ----------------------------------------------------------------------------------------------- submit
def submit(conn: Connection, ctx: RequestContext, cs_id: UUID) -> str:
    """OPEN → IN_REVIEW. Deterministic metadata facts of low significance are auto-verified."""
    cs = _lock_change_set(conn, cs_id)
    if cs.status != "OPEN":
        raise ReviewError("CHANGE_SET_NOT_OPEN", f"change set is {cs.status}")
    if cs.created_by != ctx.principal.principal_id:
        raise ReviewError("SUBMIT_FORBIDDEN", "only the author can submit a change set")
    auto = 0
    for item in _items(conn, cs_id, for_update=True):
        state = VersionState(item.status, item.basis, item.significance)
        if (
            item.status is Status.OBSERVED
            and item.basis in DETERMINISTIC_BASES
            and item.significance <= Significance.LOW
        ):
            check_transition(state, Status.VERIFIED)
            _promote(conn, item, item.basis, None)
            auto += 1
        else:
            check_transition(state, Status.UNDER_REVIEW)
            _set_status(conn, item.version_id, Status.UNDER_REVIEW, item.basis, None)
    new_status = _refresh_change_set_status(conn, cs_id)
    audit.record(
        conn,
        ctx,
        "change_set.submitted",
        tenant_id=cs.tenant_id,
        object_ref=f"change_set:{cs_id}",
        details={"auto_verified": auto, "status": new_status},
    )
    return new_status


# ----------------------------------------------------------------------------------------------- decide
def _signed_hash(cs_id: UUID, item: _ItemRow, reviewer: UUID, role: str, d: DecisionInput) -> bytes:
    # Phase 1: content-bound SHA-256. Phase 2 replaces this with a KMS signature over the same payload.
    payload = {
        "change_set_id": str(cs_id),
        "change_item_id": str(item.change_item_id),
        "version_content_sha256": item.content_hash.hex(),
        "reviewer_id": str(reviewer),
        "reviewer_role": role,
        "decision": d.decision.value,
        "rationale": d.rationale,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).digest()


def _approvals(conn: Connection, change_item_id: UUID, roles: RoleRegistry) -> list[Approval]:
    out: list[Approval] = []
    for r in conn.execute(
        text("SELECT reviewer_id, reviewer_role, decision FROM review_decision WHERE change_item_id = :i"),
        {"i": change_item_id},
    ):
        profile = roles.get(r.reviewer_role)
        authority = profile.authority() if profile else None
        if authority is not None:
            out.append(Approval(r.reviewer_id, Decision(r.decision), authority))
    return out


def _reviewer_authority(ctx: RequestContext, role: str, roles: RoleRegistry) -> ReviewAuthority:
    principal = ctx.principal
    if principal.kind is not PrincipalKind.HUMAN:
        raise ReviewError("HUMAN_REVIEW_REQUIRED", "review decisions can only be made by human principals")
    if role not in principal.roles:
        raise ReviewError("ROLE_NOT_GRANTED", f"principal does not hold role {role!r}")
    profile = roles.get(role)
    authority = profile.authority() if profile else None
    if authority is None:
        raise ReviewError("NO_REVIEW_AUTHORITY", f"role {role!r} has no review authority")
    return authority


def _check_reviewable(ctx: RequestContext, cs: _ChangeSet) -> None:
    if cs.status != "IN_REVIEW":
        raise ReviewError("CHANGE_SET_NOT_IN_REVIEW", f"change set is {cs.status}")
    if cs.created_by == ctx.principal.principal_id:
        raise ReviewError("SELF_REVIEW_FORBIDDEN", "the author of a change set cannot review it")


def _check_batch(decisions: list[DecisionInput], items: dict[UUID, _ItemRow]) -> None:
    if not decisions:
        raise ReviewError("NO_DECISIONS", "at least one decision is required")
    if len({d.change_item_id for d in decisions}) != len(decisions):
        raise ReviewError("DUPLICATE_ITEM", "each item may appear once per request")
    for d in decisions:
        if d.change_item_id not in items:
            raise ReviewError("NOT_FOUND", f"change item {d.change_item_id} is not in this change set")
    approving = {Decision.APPROVE, Decision.APPROVE_PROVISIONAL}
    if len(decisions) > 1 and any(
        d.decision in approving and items[d.change_item_id].materiality == "FINANCIAL" for d in decisions
    ):
        raise ReviewError("BULK_APPROVAL_FORBIDDEN", "FINANCIAL items must be approved one at a time")


def _apply(  # noqa: PLR0917 - internal helper, all arguments required
    conn: Connection,
    item: _ItemRow,
    state: VersionState,
    decision: Decision,
    decision_id: UUID,
    approvals: list[Approval],
    approvers: int,
) -> Status:
    """Transition the item's version for a recorded decision; returns its resulting status."""
    simple = {
        Decision.REJECT: Status.REJECTED,
        Decision.REQUEST_CHANGES: Status.CANDIDATE,
        Decision.APPROVE_PROVISIONAL: Status.PROVISIONAL,
    }
    if decision in simple:
        target = simple[decision]
        basis = check_transition(state, target, approvals=approvals)
        _set_status(conn, item.version_id, target, basis, decision_id)
        return target
    if approvers < required_approvals(item.significance):
        return item.status  # waiting for a further approver (four-eyes)
    new_basis = Basis.SPECIALIST_APPROVED if item.basis in (None, Basis.AI_INFERRED) else item.basis
    basis = check_transition(state, Status.VERIFIED, new_basis=new_basis, approvals=approvals)
    _promote(conn, item, basis, decision_id)
    return Status.VERIFIED


def _decide_item(
    conn: Connection,
    ctx: RequestContext,
    cs: _ChangeSet,
    item: _ItemRow,
    d: DecisionInput,
    *,
    role: str,
    authority: ReviewAuthority,
    roles: RoleRegistry,
) -> DecisionResult:
    reviewer = ctx.principal.principal_id
    entity = load_entity(conn, item.entity_id)
    assert entity is not None  # noqa: S101 - the version references it
    state = VersionState(item.status, item.basis, item.significance, entity.domain_codes, entity.kind)
    if item.status not in {Status.UNDER_REVIEW, Status.PROVISIONAL}:
        raise ReviewError("ITEM_NOT_REVIEWABLE", f"item is {item.status}")
    if not authority.covers(entity.domain_codes, item.significance, entity.kind):
        raise ReviewError(
            "REVIEW_AUTHORITY_INSUFFICIENT",
            f"role {role!r} cannot review {entity.kind} with significance {item.significance.name}",
        )
    if conn.execute(
        text("SELECT 1 FROM review_decision WHERE change_item_id = :i AND reviewer_id = :r"),
        {"i": item.change_item_id, "r": reviewer},
    ).first():
        raise ReviewError("ALREADY_DECIDED", "reviewer has already decided on this item")

    decision_id: UUID = conn.execute(
        text(
            "INSERT INTO review_decision (tenant_id, change_set_id, change_item_id, reviewer_id, "
            "reviewer_role, decision, rationale, signed_hash) "
            "VALUES (:t, :cs, :i, :r, :role, :d, :why, :h) RETURNING review_decision_id"
        ),
        {
            "t": cs.tenant_id,
            "cs": cs.change_set_id,
            "i": item.change_item_id,
            "r": reviewer,
            "role": role,
            "d": d.decision.value,
            "why": d.rationale,
            "h": _signed_hash(cs.change_set_id, item, reviewer, role, d),
        },
    ).scalar_one()
    approvals = _approvals(conn, item.change_item_id, roles)
    approvers = {
        a.reviewer_id
        for a in approvals
        if a.decision is Decision.APPROVE
        and a.authority.covers(state.domains, state.significance, state.kind)
    }
    new_status = _apply(conn, item, state, d.decision, decision_id, approvals, len(approvers))
    audit.record(
        conn,
        ctx,
        "review.decision",
        tenant_id=cs.tenant_id,
        role=role,
        object_ref=f"change_item:{item.change_item_id}",
        details={
            "change_set_id": str(cs.change_set_id),
            "decision": d.decision.value,
            "review_decision_id": str(decision_id),
            "status": new_status.value,
        },
    )
    return DecisionResult(
        item.change_item_id, decision_id, new_status, len(approvers), required_approvals(item.significance)
    )


def decide(
    conn: Connection,
    ctx: RequestContext,
    cs_id: UUID,
    decisions: list[DecisionInput],
    *,
    role: str,
    roles: RoleRegistry,
) -> list[DecisionResult]:
    authority = _reviewer_authority(ctx, role, roles)
    cs = _lock_change_set(conn, cs_id)
    _check_reviewable(ctx, cs)
    items = {i.change_item_id: i for i in _items(conn, cs_id, for_update=True)}
    _check_batch(decisions, items)
    results = [
        _decide_item(conn, ctx, cs, items[d.change_item_id], d, role=role, authority=authority, roles=roles)
        for d in decisions
    ]
    _refresh_change_set_status(conn, cs_id)
    return results


# ----------------------------------------------------------------------------------------------- read
def list_change_sets(conn: Connection, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT change_set_id, tenant_id, origin, title, status, risk_level, created_by, created_at "
            "FROM change_set WHERE (CAST(:s AS text) IS NULL OR status = :s) "
            "ORDER BY created_at DESC LIMIT :n"
        ),
        {"s": status, "n": limit},
    ).mappings()
    return [dict(r) for r in rows]


def get_change_set(conn: Connection, cs_id: UUID) -> dict[str, Any] | None:
    cs = conn.execute(
        text(
            "SELECT change_set_id, tenant_id, origin, title, status, risk_level, created_by, created_at "
            "FROM change_set WHERE change_set_id = :cs"
        ),
        {"cs": cs_id},
    ).mappings().first()  # fmt: skip
    if cs is None:
        return None
    items = conn.execute(
        text(
            """SELECT ci.change_item_id, ci.op, ci.materiality, ci.base_version_id, ci.diff,
                      e.entity_id, e.kind, e.canonical_key, e.scope, e.domain_codes,
                      v.version_id, v.version_no, v.status, v.name, v.definition, v.verification_basis,
                      v.financial_significance, v.epistemic_class
               FROM change_item ci
               JOIN entity_version v ON v.version_id = ci.entity_version_id
               JOIN knowledge_entity e ON e.entity_id = v.entity_id
               WHERE ci.change_set_id = :cs ORDER BY ci.change_item_id"""
        ),
        {"cs": cs_id},
    ).mappings()
    decisions = conn.execute(
        text(
            "SELECT review_decision_id, change_item_id, reviewer_id, reviewer_role, decision, rationale, "
            "decided_at FROM review_decision WHERE change_set_id = :cs ORDER BY decided_at"
        ),
        {"cs": cs_id},
    ).mappings()
    by_item: dict[UUID, list[dict[str, Any]]] = {}
    for dec in decisions:
        by_item.setdefault(dec["change_item_id"], []).append(dict(dec))
    out = dict(cs)
    out["items"] = [{**dict(i), "decisions": by_item.get(i["change_item_id"], [])} for i in items]
    return out
