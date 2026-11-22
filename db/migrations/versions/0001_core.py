"""Core knowledge model, tenant isolation (RLS), lifecycle enforcement and audit chain.

Revision ID: 0001_core
Revises:
Create Date: 2026-11-20
"""

from pathlib import Path

from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "0001_core.sql"

# --- Table classes for row-level security --------------------------------------------------------------
# TENANT: rows always belong to exactly one tenant.
TENANT_TABLES = [
    "site",
    "system_instance",
    "environment",
    "source_system",
    "consumer_artifact",
    "artifact_dependency",
    "discovery_run",
    "snapshot",
]
# MIXED: tenant_id NULL means shared (generic/product/Svarm) knowledge readable by every active context.
MIXED_TABLES = [
    "source_artifact",
    "evidence",
    "knowledge_entity",
    "entity_version",
    "entity_head",
    "knowledge_relation",
    "relation_version",
    "evidence_link",
    "change_set",
    "change_item",
    "review_decision",
    "knowledge_package",
    "package_version",
    "package_member",
]
# CATALOG: no tenant column; readable by any active context, writable only with shared-write permission.
CATALOG_TABLES = ["licence_policy", "product", "product_release", "entity_kind", "relation_kind"]
# READ_ONLY: rules data that only migrations may change.
READ_ONLY_TABLES = ["lifecycle_transition"]

ACTIVE = "gnosis_ctx_active()"
IN_TENANT = "tenant_id = ANY (gnosis_ctx_tenants())"
SHARED_WRITE = "(tenant_id IS NULL AND gnosis_ctx_can_write_shared())"


def _policies() -> list[str]:
    stmts: list[str] = []

    def enable(table: str) -> None:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    for t in TENANT_TABLES:
        enable(t)
        cond = f"{ACTIVE} AND {IN_TENANT}"
        stmts.append(f"CREATE POLICY {t}_select ON {t} FOR SELECT USING ({cond})")
        stmts.append(f"CREATE POLICY {t}_insert ON {t} FOR INSERT WITH CHECK ({cond})")
        stmts.append(f"CREATE POLICY {t}_update ON {t} FOR UPDATE USING ({cond}) WITH CHECK ({cond})")
        stmts.append(f"GRANT SELECT, INSERT, UPDATE ON {t} TO gnosis_app")

    for t in MIXED_TABLES:
        enable(t)
        read = f"{ACTIVE} AND (tenant_id IS NULL OR {IN_TENANT})"
        write = f"{ACTIVE} AND ({IN_TENANT} OR {SHARED_WRITE})"
        stmts.append(f"CREATE POLICY {t}_select ON {t} FOR SELECT USING ({read})")
        stmts.append(f"CREATE POLICY {t}_insert ON {t} FOR INSERT WITH CHECK ({write})")
        stmts.append(f"CREATE POLICY {t}_update ON {t} FOR UPDATE USING ({write}) WITH CHECK ({write})")
        stmts.append(f"GRANT SELECT, INSERT, UPDATE ON {t} TO gnosis_app")

    for t in CATALOG_TABLES:
        enable(t)
        write = f"{ACTIVE} AND gnosis_ctx_can_write_shared()"
        stmts.append(f"CREATE POLICY {t}_select ON {t} FOR SELECT USING ({ACTIVE})")
        stmts.append(f"CREATE POLICY {t}_insert ON {t} FOR INSERT WITH CHECK ({write})")
        stmts.append(f"CREATE POLICY {t}_update ON {t} FOR UPDATE USING ({write}) WITH CHECK ({write})")
        stmts.append(f"GRANT SELECT, INSERT, UPDATE ON {t} TO gnosis_app")

    for t in READ_ONLY_TABLES:
        enable(t)
        stmts.append(f"CREATE POLICY {t}_select ON {t} FOR SELECT USING ({ACTIVE})")
        stmts.append(f"GRANT SELECT ON {t} TO gnosis_app")

    # tenant: visible only to principals entitled to it; created by platform admins (shared-write).
    enable("tenant")
    stmts.append(f"CREATE POLICY tenant_select ON tenant FOR SELECT USING ({ACTIVE} AND {IN_TENANT})")
    stmts.append(
        f"CREATE POLICY tenant_insert ON tenant FOR INSERT WITH CHECK "
        f"({ACTIVE} AND gnosis_ctx_can_write_shared())"
    )
    stmts.append("GRANT SELECT, INSERT ON tenant TO gnosis_app")

    # audit_event: append-only. Reads limited to own tenants (+ platform events for shared writers).
    enable("audit_event")
    stmts.append(
        f"CREATE POLICY audit_select ON audit_event FOR SELECT USING "
        f"({ACTIVE} AND ({IN_TENANT} OR {SHARED_WRITE}))"
    )
    stmts.append(
        f"CREATE POLICY audit_insert ON audit_event FOR INSERT WITH CHECK "
        f"({ACTIVE} AND ({IN_TENANT} OR tenant_id IS NULL))"
    )
    stmts.append("GRANT SELECT, INSERT ON audit_event TO gnosis_app")
    stmts.append("GRANT USAGE ON SEQUENCE audit_event_seq_seq TO gnosis_app")
    stmts.append("REVOKE ALL ON FUNCTION gnosis_verify_audit_chain() FROM PUBLIC")
    return stmts


ALL_RLS_TABLES = TENANT_TABLES + MIXED_TABLES + CATALOG_TABLES + READ_ONLY_TABLES + ["tenant", "audit_event"]


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection  # psycopg 3 connection, same transaction
    with raw.cursor() as cur:
        cur.execute(SQL_FILE.read_text(encoding="utf-8"))
        for stmt in _policies():
            cur.execute(stmt)


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (expand/contract); see docs/architecture/11 §6")
