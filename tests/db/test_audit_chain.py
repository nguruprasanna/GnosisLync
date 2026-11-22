"""Append-only, hash-chained audit log (doc 08 §9)."""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from gnosis.context.db import tenant_transaction
from tests.db.conftest import World, ctx_for

pytestmark = [pytest.mark.db, pytest.mark.security]


def _append(app_engine: Engine, w: World, action: str) -> None:
    with tenant_transaction(app_engine, ctx_for(w.tenant_a)) as c:
        c.execute(
            text(
                "INSERT INTO audit_event (tenant_id, actor, action, details, prev_hash, hash) "
                "VALUES (:t, 'user:test', :a, '{}', '', '')"
            ),
            {"t": w.tenant_a, "a": action},
        )


def test_chain_verifies_after_appends(app_engine: Engine, owner_engine: Engine, world: World) -> None:
    for i in range(3):
        _append(app_engine, world, f"TEST_{i}")
    with owner_engine.connect() as c:
        assert c.execute(text("SELECT gnosis_verify_audit_chain()")).scalar_one() is None


def test_client_supplied_hashes_are_ignored(app_engine: Engine, world: World) -> None:
    _append(app_engine, world, "TEST_HASH")
    with tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        h = c.execute(text("SELECT hash FROM audit_event ORDER BY seq DESC LIMIT 1")).scalar_one()
    assert len(h) == 32


@pytest.mark.parametrize("stmt", ["UPDATE audit_event SET action = 'X'", "DELETE FROM audit_event"])
def test_app_role_cannot_rewrite_audit(app_engine: Engine, world: World, stmt: str) -> None:
    _append(app_engine, world, "TEST_RW")
    with (
        pytest.raises(DBAPIError, match="permission denied"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        c.execute(text(stmt))


def test_even_owner_cannot_update_audit(owner_engine: Engine, app_engine: Engine, world: World) -> None:
    _append(app_engine, world, "TEST_OWNER")
    with pytest.raises(DBAPIError, match="append-only"), owner_engine.begin() as c:
        c.execute(text("UPDATE audit_event SET action = 'X'"))


def test_tampering_is_detected(owner_engine: Engine, app_engine: Engine, world: World) -> None:
    _append(app_engine, world, "TEST_TAMPER")
    with owner_engine.connect() as c:
        tx = c.begin()  # rolled back: the forged row never persists
        seq = c.execute(text("SELECT max(seq) FROM audit_event")).scalar_one()
        c.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_update"))
        c.execute(text("UPDATE audit_event SET details = '{\"forged\": true}' WHERE seq = :s"), {"s": seq})
        c.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_update"))
        assert c.execute(text("SELECT gnosis_verify_audit_chain()")).scalar_one() == seq
        tx.rollback()


def test_other_tenant_cannot_read_audit(app_engine: Engine, world: World) -> None:
    marker = f"TEST_{uuid.uuid4().hex}"
    _append(app_engine, world, marker)
    with tenant_transaction(app_engine, ctx_for(world.tenant_b)) as c:
        assert (
            c.execute(text("SELECT count(*) FROM audit_event WHERE action = :a"), {"a": marker}).scalar_one()
            == 0
        )
