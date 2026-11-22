"""Database backstop for lifecycle invariants (doc 02 §4.1; doc 09 §5 'inferred marked as verified',
'unauthorised modification of approved knowledge')."""

import hashlib
import uuid

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from gnosis.context.db import tenant_transaction
from tests.db.conftest import World, ctx_for, sha

pytestmark = [pytest.mark.db, pytest.mark.knowledge]


def _new_version(
    c: Connection, w: World, status: str, basis: str | None, significance: str = "NONE"
) -> uuid.UUID:
    ent = c.execute(
        text(
            "INSERT INTO knowledge_entity (tenant_id, scope, kind, canonical_key, environment_id) "
            "VALUES (:t, 'SITE', 'site_field', :k, :e) RETURNING entity_id"
        ),
        {"t": w.tenant_a, "k": f"k:{uuid.uuid4()}", "e": w.env_a},
    ).scalar_one()
    return c.execute(
        text("""
        INSERT INTO entity_version (entity_id, tenant_id, version_no, status, name, epistemic_class,
                                    verification_basis, financial_significance, produced_by, content_hash)
        VALUES (:e, :t, 1, :s, 'n', 'FACT', :b, :f, 'test', :h) RETURNING version_id"""),
        {"e": ent, "t": w.tenant_a, "s": status, "b": basis, "f": significance, "h": sha(str(ent))},
    ).scalar_one()


def _decision(c: Connection, w: World, decision: str) -> uuid.UUID:
    cs = c.execute(
        text(
            "INSERT INTO change_set (tenant_id, origin, title) VALUES (:t, 'MANUAL', 'cs') "
            "RETURNING change_set_id"
        ),
        {"t": w.tenant_a},
    ).scalar_one()
    return c.execute(
        text("""
        INSERT INTO review_decision (tenant_id, change_set_id, reviewer_id, reviewer_role, decision,
                                     rationale,
                                     signed_hash)
        VALUES (:t, :cs, :r, 'lending_aa_specialist', :d, 'checked against evidence', :h)
        RETURNING review_decision_id"""),
        {"t": w.tenant_a, "cs": cs, "r": uuid.uuid4(), "d": decision, "h": hashlib.sha256(b"sig").digest()},
    ).scalar_one()


def _set(
    c: Connection,
    version: uuid.UUID,
    status: str,
    basis: str | None = None,
    decision: uuid.UUID | None = None,
) -> None:
    c.execute(
        text(
            "UPDATE entity_version SET status = :s, verification_basis = coalesce(:b, verification_basis), "
            "review_decision_id = :d WHERE version_id = :v"
        ),
        {"s": status, "b": basis, "d": decision, "v": version},
    )


def test_candidate_cannot_become_verified_directly(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="illegal transition CANDIDATE -> VERIFIED"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        v = _new_version(c, world, "CANDIDATE", "AI_INFERRED")
        d = _decision(c, world, "APPROVE")
        _set(c, v, "VERIFIED", "SPECIALIST_APPROVED", d)


def test_new_version_cannot_start_verified(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="must start as"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        _new_version(c, world, "VERIFIED", "SOURCE_METADATA")


def test_observed_requires_deterministic_basis(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="OBSERVED requires"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        _new_version(c, world, "OBSERVED", "AI_INFERRED")


def test_ai_inferred_cannot_be_verified_even_with_approval(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="non-AI verification basis"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        v = _new_version(c, world, "CANDIDATE", "AI_INFERRED")
        _set(c, v, "UNDER_REVIEW")
        _set(c, v, "VERIFIED", None, _decision(c, world, "APPROVE"))


def test_reviewed_candidate_can_be_verified(app_engine: Engine, world: World) -> None:
    with tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        v = _new_version(c, world, "CANDIDATE", "AI_INFERRED", "HIGH")
        _set(c, v, "UNDER_REVIEW")
        _set(c, v, "VERIFIED", "SPECIALIST_APPROVED", _decision(c, world, "APPROVE"))
        assert (
            c.execute(text("SELECT status FROM entity_version WHERE version_id = :v"), {"v": v}).scalar_one()
            == "VERIFIED"
        )


def test_observed_metadata_auto_verifies_when_not_financial(app_engine: Engine, world: World) -> None:
    with tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        v = _new_version(c, world, "OBSERVED", "SOURCE_METADATA", "LOW")
        _set(c, v, "VERIFIED")


def test_financial_observed_fact_needs_review(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="requires an APPROVE review decision"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        v = _new_version(c, world, "OBSERVED", "SOURCE_METADATA", "HIGH")
        _set(c, v, "VERIFIED")


def test_reject_decision_cannot_verify(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="decision APPROVE"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        v = _new_version(c, world, "CANDIDATE", "DOCUMENTATION")
        _set(c, v, "UNDER_REVIEW")
        _set(c, v, "VERIFIED", None, _decision(c, world, "REJECT"))


def test_verified_content_is_immutable(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="immutable"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        v = _new_version(c, world, "OBSERVED", "SOURCE_METADATA")
        _set(c, v, "VERIFIED")
        c.execute(
            text("UPDATE entity_version SET definition = 'silently changed' WHERE version_id = :v"), {"v": v}
        )


def test_basis_cannot_change_without_transition(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(IntegrityError, match="only change together"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        v = _new_version(c, world, "CANDIDATE", "AI_INFERRED")
        c.execute(
            text(
                "UPDATE entity_version SET verification_basis = 'SPECIALIST_APPROVED' WHERE version_id = :v"
            ),
            {"v": v},
        )


def test_review_decisions_are_immutable(app_engine: Engine, world: World) -> None:
    from sqlalchemy.exc import ProgrammingError

    with (
        pytest.raises(ProgrammingError, match="immutable"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        d = _decision(c, world, "REJECT")
        c.execute(
            text("UPDATE review_decision SET decision = 'APPROVE' WHERE review_decision_id = :d"), {"d": d}
        )
