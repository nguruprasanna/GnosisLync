"""Structural security checks on the migrated schema."""

import pytest
from sqlalchemy import Engine, text

from gnosis.knowledge.lifecycle import TRANSITIONS
from tests.db.conftest import APP_ROLE

pytestmark = [pytest.mark.db, pytest.mark.security]


def test_every_table_has_row_level_security(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        missing = (
            c.execute(
                text("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
              AND NOT c.relrowsecurity
        """)
            )
            .scalars()
            .all()
        )
    assert missing == []


def test_app_role_has_no_bypass_and_owns_nothing(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        for role in ("gnosis_app", APP_ROLE):
            bypass, superuser = c.execute(
                text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = :r"), {"r": role}
            ).one()
            assert not bypass
            assert not superuser
            owned = c.execute(
                text("""
                SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND pg_get_userbyid(c.relowner) = :r"""),
                {"r": role},
            ).scalar_one()
            assert owned == 0


def test_db_transitions_match_python_lifecycle(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        rows = {(f, t) for f, t in c.execute(text("SELECT from_status, to_status FROM lifecycle_transition"))}
    assert rows == {(f.value, t.value) for f, t in TRANSITIONS}


def test_app_role_cannot_modify_transition_rules(app_engine: Engine) -> None:
    from sqlalchemy.exc import ProgrammingError

    from gnosis.context.db import tenant_transaction
    from tests.db.conftest import ctx_for

    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_transaction(app_engine, ctx_for(shared_write=True)) as c,
    ):
        c.execute(text("INSERT INTO lifecycle_transition VALUES ('CANDIDATE', 'VERIFIED')"))
