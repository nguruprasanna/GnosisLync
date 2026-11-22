"""Database fixtures.

Uses ``GNOSIS_TEST_DATABASE_URL`` (schema-owner/superuser URL of an empty database) if set, otherwise
starts ``pgvector/pgvector:pg16`` with Testcontainers. Migrations run once per session as the owner; tests
use a separate LOGIN role that is a member of ``gnosis_app`` (subject to RLS).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from gnosis.context import Principal, Purpose, RequestContext

ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "gnosis_api_test"
APP_PASSWORD = "test-only-password"


@pytest.fixture(scope="session")
def owner_url() -> Iterator[str]:
    external = os.environ.get("GNOSIS_TEST_DATABASE_URL")
    if external:
        yield external
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def migrated(owner_url: str) -> str:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    eng = create_engine(owner_url)
    with eng.begin() as c:
        c.execute(
            text(f"""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' IN ROLE gnosis_app;
              END IF;
            END $$;""")
        )
    eng.dispose()
    return owner_url


@pytest.fixture(scope="session")
def owner_engine(migrated: str) -> Iterator[Engine]:
    eng = create_engine(migrated)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def app_url(migrated: str) -> str:
    url = make_url(migrated).set(username=APP_ROLE, password=APP_PASSWORD)
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def app_engine(app_url: str) -> Iterator[Engine]:
    eng = create_engine(app_url)
    yield eng
    eng.dispose()


def sha(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def embedding(seed: int) -> str:
    vec = [0.0] * 1024
    vec[seed % 1024] = 1.0
    vec[(seed * 7 + 3) % 1024] = 0.5
    return "[" + ",".join(str(x) for x in vec) + "]"


@dataclass
class World:
    """Two tenants with one site environment each, plus shared generic knowledge."""

    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    code_a: str
    code_b: str
    env_a: uuid.UUID
    env_b: uuid.UUID
    entity_a: uuid.UUID
    entity_b: uuid.UUID
    entity_generic: uuid.UUID
    version_a: uuid.UUID
    version_b: uuid.UUID
    version_generic: uuid.UUID
    shared_embedding: str


def _insert(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar_one()


@pytest.fixture
def world(owner_engine: Engine) -> World:
    sfx = uuid.uuid4().hex[:8].upper()
    shared_emb = embedding(int(sfx, 16))
    with owner_engine.begin() as c:
        product = _insert(
            c,
            "INSERT INTO product (code, vendor, pack_module) VALUES (:c, 'Temenos', "
            "'gnosis.packs.temenos_transact') RETURNING product_id",
            c=f"TT_{sfx}",
        )
        release = _insert(
            c,
            "INSERT INTO product_release (product_id, code, ordinal) VALUES (:p, 'R22', 22) "
            "RETURNING release_id",
            p=product,
        )
        c.execute(
            text(
                "INSERT INTO entity_kind (kind, allowed_scopes, pack) VALUES "
                "('banking_concept', '{GENERIC}', 'banking_core'), "
                "('site_field', '{SITE}', 'site_core') ON CONFLICT DO NOTHING"
            )
        )
        tenants, envs = [], []
        for name in ("A", "B"):
            t = _insert(
                c,
                "INSERT INTO tenant (code, name, deployment_mode, llm_policy_ref, data_region) "
                "VALUES (:c, :c, 'SHARED', 'default', 'EU') RETURNING tenant_id",
                c=f"{name}{sfx}",
            )
            s = _insert(
                c, "INSERT INTO site (tenant_id, code, name) VALUES (:t, 'S1', 'Site') RETURNING site_id", t=t
            )
            i = _insert(
                c,
                "INSERT INTO system_instance (tenant_id, site_id, product_id, code) "
                "VALUES (:t, :s, :p, 'T24-PRIMARY') RETURNING instance_id",
                t=t,
                s=s,
                p=product,
            )
            e = _insert(
                c,
                "INSERT INTO environment (tenant_id, instance_id, code, env_class, release_id, runtime) "
                "VALUES (:t, :i, 'UAT', 'UAT', :r, 'TAFJ') RETURNING environment_id",
                t=t,
                i=i,
                r=release,
            )
            tenants.append(t)
            envs.append(e)

        def entity(
            tenant: uuid.UUID | None, env: uuid.UUID | None, kind: str, scope: str, key: str, name: str
        ) -> tuple[uuid.UUID, uuid.UUID]:
            ent = _insert(
                c,
                "INSERT INTO knowledge_entity (tenant_id, scope, kind, canonical_key, environment_id) "
                "VALUES (:t, :s, :k, :key, :e) RETURNING entity_id",
                t=tenant,
                s=scope,
                k=kind,
                key=key,
                e=env,
            )
            ver = _insert(
                c,
                """INSERT INTO entity_version (entity_id, tenant_id, version_no, status, name, definition,
                       epistemic_class, verification_basis, produced_by, content_hash, embedding)
                   VALUES (:ent, :t, 1, 'OBSERVED', :n, 'Balance of the loan', 'FACT',
                           'SOURCE_METADATA', 'test', :h, CAST(:emb AS vector))
                   RETURNING version_id""",
                ent=ent,
                t=tenant,
                n=name,
                h=sha(key),
                emb=shared_emb,
            )
            c.execute(
                text("INSERT INTO entity_head (entity_id, tenant_id, working_version) VALUES (:e, :t, :v)"),
                {"e": ent, "t": tenant, "v": ver},
            )
            return ent, ver

        ea, va = entity(
            tenants[0], envs[0], "site_field", "SITE", f"site:t24:ECB>OPEN.BALANCE:{sfx}", "OPEN.BALANCE A"
        )
        eb, vb = entity(
            tenants[1], envs[1], "site_field", "SITE", f"site:t24:ECB>OPEN.BALANCE:{sfx}", "OPEN.BALANCE B"
        )
        eg, vg = entity(
            None, None, "banking_concept", "GENERIC", f"concept:lending.principal:{sfx}", "Principal"
        )
    return World(
        tenants[0], tenants[1], f"A{sfx}", f"B{sfx}", envs[0], envs[1], ea, eb, eg, va, vb, vg, shared_emb
    )


def ctx_for(*tenants: uuid.UUID, shared_write: bool = False, subject: str = "user:test") -> RequestContext:
    principal = Principal(subject=subject, tenant_ids=frozenset(tenants), can_write_shared=shared_write)
    return RequestContext.build(principal, None, Purpose.KNOWLEDGE_ADMIN)
