"""Tenant isolation through row-level security (docs/architecture/08 §4, doc 09 §5 'cross-bank access')."""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from gnosis.context.db import tenant_transaction
from tests.db.conftest import World, ctx_for

pytestmark = [pytest.mark.db, pytest.mark.security]


def _visible_entities(app_engine: Engine, *tenants: uuid.UUID) -> set[uuid.UUID]:
    with tenant_transaction(app_engine, ctx_for(*tenants)) as c:
        return set(c.execute(text("SELECT entity_id FROM knowledge_entity")).scalars())


def test_tenant_sees_own_and_shared_but_not_other_tenant(app_engine: Engine, world: World) -> None:
    visible = _visible_entities(app_engine, world.tenant_a)
    assert world.entity_a in visible
    assert world.entity_generic in visible
    assert world.entity_b not in visible


def test_no_context_returns_zero_rows_even_for_shared_knowledge(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM knowledge_entity")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM tenant")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM product")).scalar_one() == 0


def test_settings_are_transaction_local(app_engine: Engine, world: World) -> None:
    with tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM knowledge_entity WHERE entity_id = :e"), {"e": world.entity_a}
            ).scalar_one()
            == 1
        )
    # Same pooled connection, new transaction: context must be gone.
    with app_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM knowledge_entity")).scalar_one() == 0


def test_vector_search_does_not_return_other_tenant_neighbours(app_engine: Engine, world: World) -> None:
    with tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        ids = set(
            c.execute(
                text(
                    "SELECT entity_id FROM entity_version ORDER BY embedding <=> CAST(:q AS vector) LIMIT 50"
                ),
                {"q": world.shared_embedding},
            ).scalars()
        )
    assert world.entity_b not in ids
    assert world.entity_a in ids


def test_full_text_search_respects_tenant(app_engine: Engine, world: World) -> None:
    with tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        names = set(
            c.execute(
                text(
                    "SELECT name FROM entity_version "
                    "WHERE search_tsv @@ plainto_tsquery('simple', 'OPEN.BALANCE')"
                )
            ).scalars()
        )
    assert "OPEN.BALANCE A" in names
    assert "OPEN.BALANCE B" not in names


def test_cannot_insert_into_other_tenant(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        c.execute(
            text(
                "INSERT INTO knowledge_entity (tenant_id, scope, kind, canonical_key, environment_id) "
                "VALUES (:t, 'SITE', 'site_field', 'x', :e)"
            ),
            {"t": world.tenant_b, "e": world.env_b},
        )


def test_cannot_write_shared_knowledge_without_permission(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        c.execute(
            text(
                "INSERT INTO knowledge_entity (scope, kind, canonical_key) "
                "VALUES ('GENERIC', 'banking_concept', :k)"
            ),
            {"k": f"concept:x:{uuid.uuid4()}"},
        )


def test_shared_writer_can_write_shared_knowledge(app_engine: Engine, world: World) -> None:
    with tenant_transaction(app_engine, ctx_for(world.tenant_a, shared_write=True)) as c:
        c.execute(
            text(
                "INSERT INTO knowledge_entity (scope, kind, canonical_key) "
                "VALUES ('GENERIC', 'banking_concept', :k)"
            ),
            {"k": f"concept:x:{uuid.uuid4()}"},
        )


def test_cannot_move_row_to_other_tenant_by_update(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        c.execute(
            text("UPDATE site SET tenant_id = :b WHERE tenant_id = :a"),
            {"a": world.tenant_a, "b": world.tenant_b},
        )


def test_update_of_other_tenant_rows_affects_nothing(app_engine: Engine, world: World) -> None:
    with tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        res = c.execute(text("UPDATE site SET name = 'pwned' WHERE tenant_id = :b"), {"b": world.tenant_b})
        assert res.rowcount == 0


def test_app_role_cannot_delete(app_engine: Engine, world: World) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c,
    ):
        c.execute(text("DELETE FROM knowledge_entity WHERE entity_id = :e"), {"e": world.entity_a})


def test_credentials_cannot_be_stored_inline(app_engine: Engine, world: World) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), tenant_transaction(app_engine, ctx_for(world.tenant_a)) as c:
        c.execute(
            text(
                "INSERT INTO source_system (tenant_id, environment_id, kind, name, connection_ref) "
                "VALUES (:t, :e, 'SQL', 'x', 'oracle://user:secret@host/db')"
            ),
            {"t": world.tenant_a, "e": world.env_a},
        )
