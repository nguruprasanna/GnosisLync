"""Local hands-on demo for Phase 0. DEV ONLY — uses the fixed dev passwords from deploy/dev.

    uv run python scripts/demo.py seed      # two demo banks + shared knowledge (idempotent)
    uv run python scripts/demo.py token --tenant DEMO_A [--tenant DEMO_B] [--sub user:alice]
    uv run python scripts/demo.py guards    # try to break the rules as the app role; every attempt must fail
    uv run python scripts/demo.py ids       # print the demo entity ids for curl

Environment:
    GNOSIS_MIGRATION_DATABASE_URL  owner URL (default: dev compose on localhost:55432)
    GNOSIS_DATABASE_URL            app-role URL (default from gnosis.config.Settings)
    GNOSIS_JWT_HS256_SECRET        required by `token`; must match the running API
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import jwt
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from gnosis.config import Settings
from gnosis.context import Principal, Purpose, RequestContext
from gnosis.context.db import tenant_transaction

OWNER_URL = os.environ.get(
    "GNOSIS_MIGRATION_DATABASE_URL", "postgresql+psycopg://gnosis_owner:dev-only-owner@localhost:55432/gnosis"
)
NS = uuid.UUID("6f1c2f0e-7a4b-4d8e-9a51-0c1d2e3f4a5b")


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(NS, name)


TENANTS = {"DEMO_A": ("Demo Bank A", _id("tenant:A")), "DEMO_B": ("Demo Bank B", _id("tenant:B"))}
FIELD_KEY = "site:t24:LD.LOANS.AND.DEPOSITS>OPEN.BALANCE"
ENTITIES: dict[str, uuid.UUID] = {
    "bank_a_field": _id("entity:A:open_balance"),
    "bank_b_field": _id("entity:B:open_balance"),
    "bank_a_ai_rule": _id("entity:A:ai_rule"),
    "shared_concept": _id("entity:generic:principal"),
}


def _sha(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _one(c: Connection, sql: str, **p: Any) -> Any:
    return c.execute(text(sql), p).scalar_one()


# ------------------------------------------------------------------------------------------------ seed
def _entity(  # noqa: PLR0917 — demo helper
    c: Connection,
    entity_id: uuid.UUID,
    tenant: uuid.UUID | None,
    env: uuid.UUID | None,
    kind: str,
    key: str,
    *,
    name: str,
    definition: str,
    status: str,
    basis: str,
    significance: str = "NONE",
    epistemic: str = "FACT",
    produced_by: str = "demo-seed",
) -> uuid.UUID:
    scope = "GENERIC" if tenant is None else "SITE"
    c.execute(
        text(
            "INSERT INTO knowledge_entity (entity_id, tenant_id, scope, kind, canonical_key, environment_id) "
            "VALUES (:id, :t, :s, :k, :key, :e)"
        ),
        {"id": entity_id, "t": tenant, "s": scope, "k": kind, "key": key, "e": env},
    )
    version = _one(
        c,
        """INSERT INTO entity_version (entity_id, tenant_id, version_no, status, name, definition,
               epistemic_class, verification_basis, financial_significance, produced_by, content_hash)
           VALUES (:e, :t, 1, :st, :n, :d, :ep, :b, :sig, :pb, :h) RETURNING version_id""",
        e=entity_id,
        t=tenant,
        st=status,
        n=name,
        d=definition,
        ep=epistemic,
        b=basis,
        sig=significance,
        pb=produced_by,
        h=_sha(f"{key}:{tenant}:{name}"),
    )
    c.execute(
        text("INSERT INTO entity_head (entity_id, tenant_id, working_version) VALUES (:e, :t, :v)"),
        {"e": entity_id, "t": tenant, "v": version},
    )
    return uuid.UUID(str(version))


def seed() -> None:
    eng = create_engine(OWNER_URL)
    with eng.begin() as c:
        if c.execute(text("SELECT 1 FROM tenant WHERE code = 'DEMO_A'")).first():
            print("Demo data already present.")
            ids()
            return
        product = _one(
            c,
            "INSERT INTO product (code, vendor, pack_module) VALUES ('TEMENOS_TRANSACT', 'Temenos', "
            "'gnosis.packs.temenos_transact') ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code "
            "RETURNING product_id",
        )
        release = _one(
            c,
            "INSERT INTO product_release (product_id, code, ordinal) VALUES (:p, 'R22', 22) "
            "ON CONFLICT (product_id, code) DO UPDATE SET code = EXCLUDED.code RETURNING release_id",
            p=product,
        )
        c.execute(
            text(
                "INSERT INTO entity_kind (kind, allowed_scopes, pack) VALUES "
                "('banking_concept', '{GENERIC}', 'banking_core'), "
                "('site_field', '{SITE}', 'site_core'), "
                "('business_rule', '{SITE}', 'site_core') ON CONFLICT DO NOTHING"
            )
        )
        envs: dict[str, uuid.UUID] = {}
        for code, (name, tid) in TENANTS.items():
            c.execute(
                text(
                    "INSERT INTO tenant (tenant_id, code, name, deployment_mode, llm_policy_ref, "
                    "data_region) VALUES (:id, :c, :n, 'SHARED', 'default', 'EU')"
                ),
                {"id": tid, "c": code, "n": name},
            )
            site = _one(
                c,
                "INSERT INTO site (tenant_id, code, name) VALUES (:t, 'HQ', :n) RETURNING site_id",
                t=tid,
                n=name,
            )
            inst = _one(
                c,
                "INSERT INTO system_instance (tenant_id, site_id, product_id, code) "
                "VALUES (:t, :s, :p, 'T24-PRIMARY') RETURNING instance_id",
                t=tid,
                s=site,
                p=product,
            )
            envs[code] = _one(
                c,
                "INSERT INTO environment (tenant_id, instance_id, code, env_class, release_id, runtime) "
                "VALUES (:t, :i, 'UAT', 'UAT', :r, 'TAFJ') RETURNING environment_id",
                t=tid,
                i=inst,
                r=release,
            )

        ta, tb = TENANTS["DEMO_A"][1], TENANTS["DEMO_B"][1]
        _entity(
            c, ENTITIES["bank_a_field"], ta, envs["DEMO_A"], "site_field", FIELD_KEY,
            name="OPEN.BALANCE (Bank A)",
            definition="Bank A: opening principal balance at contract start, excluding capitalised fees.",
            status="OBSERVED", basis="SOURCE_METADATA",
        )  # fmt: skip
        _entity(
            c, ENTITIES["bank_b_field"], tb, envs["DEMO_B"], "site_field", FIELD_KEY,
            name="OPEN.BALANCE (Bank B)",
            definition="Bank B: opening balance INCLUDING capitalised fees (local customisation).",
            status="OBSERVED", basis="SOURCE_METADATA",
        )  # fmt: skip
        _entity(
            c, ENTITIES["bank_a_ai_rule"], ta, envs["DEMO_A"], "business_rule",
            "site:rule:interest-accrual-day-count",
            name="Interest accrual day count (AI-inferred)",
            definition="Suggested by AI: interest accrues ACT/360 for all loan products.",
            status="CANDIDATE", basis="AI_INFERRED", significance="HIGH",
            epistemic="INTERPRETATION", produced_by="ai:demo",
        )  # fmt: skip
        generic = _entity(
            c, ENTITIES["shared_concept"], None, None, "banking_concept", "concept:lending.principal",
            name="Principal outstanding",
            definition="Amount of a loan still owed, excluding interest and fees.",
            status="OBSERVED", basis="SOURCE_METADATA",
        )  # fmt: skip
        # Deterministic auto-verification (OBSERVED metadata fact, not financially significant).
        c.execute(text("UPDATE entity_version SET status = 'VERIFIED' WHERE version_id = :v"), {"v": generic})
        c.execute(
            text("UPDATE entity_head SET verified_version = :v WHERE entity_id = :e"),
            {"v": generic, "e": ENTITIES["shared_concept"]},
        )
        audit_rows: list[tuple[uuid.UUID | None, str]] = [
            (ta, "demo.seed"),
            (tb, "demo.seed"),
            (None, "demo.seed.shared"),
        ]
        for audit_tenant, action in audit_rows:
            c.execute(
                text(
                    "INSERT INTO audit_event (tenant_id, actor, action, details, prev_hash, hash) "
                    "VALUES (:t, 'user:demo-seed', :a, '{}', '', '')"
                ),
                {"t": audit_tenant, "a": action},
            )
    eng.dispose()
    print("Seeded Demo Bank A (DEMO_A), Demo Bank B (DEMO_B) and shared generic knowledge.")
    ids()


def ids() -> None:
    for code, (name, tid) in TENANTS.items():
        print(f"  tenant {code:<8} {tid}  ({name})")
    for label, eid in ENTITIES.items():
        print(f"  entity {label:<15} {eid}")


# ------------------------------------------------------------------------------------------------ token
def token(tenant_codes: list[str], sub: str, shared_write: bool) -> None:
    settings = Settings()
    if settings.jwt_hs256_secret is None:
        sys.exit("GNOSIS_JWT_HS256_SECRET is not set (use the same value as the running API).")
    unknown = [t for t in tenant_codes if t not in TENANTS]
    if unknown:
        sys.exit(f"Unknown tenant code(s): {unknown}. Known: {list(TENANTS)}")
    now = int(time.time())
    claims = {
        "sub": sub,
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + 8 * 3600,
        "tenants": {code: str(TENANTS[code][1]) for code in tenant_codes},
        "gnosis_shared_write": shared_write,
    }
    print(jwt.encode(claims, settings.jwt_hs256_secret.get_secret_value(), algorithm="HS256"))


# ------------------------------------------------------------------------------------------------ guards
def _attempt(label: str, fn: Callable[[], None]) -> bool:
    try:
        fn()
    except DBAPIError as exc:
        reason = str(exc.orig).splitlines()[0]
        print(f"  BLOCKED  {label}\n           -> {reason}")
        return True
    print(f"  ALLOWED  {label}   <-- this should not happen")
    return False


def guards() -> None:
    app: Engine = create_engine(Settings().database_url)
    owner: Engine = create_engine(OWNER_URL)
    ta, tb = TENANTS["DEMO_A"][1], TENANTS["DEMO_B"][1]
    ctx_a = RequestContext.build(Principal("user:alice", frozenset({ta})), None, Purpose.KNOWLEDGE_ADMIN)
    results: list[bool] = []

    print("\nAs Bank A's app session (role gnosis_api, RLS enforced):")
    with tenant_transaction(app, ctx_a) as c:
        visible = c.execute(
            text("SELECT name FROM entity_version v JOIN knowledge_entity e USING (entity_id) ORDER BY 1")
        ).scalars().all()  # fmt: skip
        print(f"  sees {len(visible)} versions: {visible}")
        leak = c.execute(
            text("SELECT count(*) FROM knowledge_entity WHERE entity_id = :b"),
            {"b": ENTITIES["bank_b_field"]},
        ).scalar_one()
        ok = leak == 0
        print(f"  {'BLOCKED ' if ok else 'ALLOWED '} reading Bank B's OPEN.BALANCE by id -> {leak} rows")
        results.append(ok)

    def in_ctx(*statements: str, **p: Any) -> Callable[[], None]:
        """Run statements in Bank A's context; never commit, even if they unexpectedly succeed."""

        def run() -> None:
            try:
                with tenant_transaction(app, ctx_a) as c:
                    for sql in statements:
                        c.execute(text(sql), p)
                    raise _Rollback
            except _Rollback:
                pass

        return run

    rule = ENTITIES["bank_a_ai_rule"]
    results.append(
        _attempt(
            "write a row into Bank B",
            in_ctx(
                "INSERT INTO knowledge_entity (tenant_id, scope, kind, canonical_key) "
                "VALUES (:b, 'SITE', 'site_field', 'site:t24:SNEAKY')",
                b=tb,
            ),
        )
    )
    results.append(
        _attempt(
            "promote the AI-inferred rule CANDIDATE -> VERIFIED",
            in_ctx("UPDATE entity_version SET status = 'VERIFIED' WHERE entity_id = :e", e=rule),
        )
    )

    review_then_verify = in_ctx(
        "UPDATE entity_version SET status = 'UNDER_REVIEW' WHERE entity_id = :e",
        "UPDATE entity_version SET status = 'VERIFIED', verification_basis = 'DOCUMENTATION' "
        "WHERE entity_id = :e",
        e=rule,
    )
    results.append(_attempt("send it to review, then mark VERIFIED with no approval", review_then_verify))
    results.append(
        _attempt(
            "edit the definition of an existing version in place",
            in_ctx(
                "UPDATE entity_version SET definition = 'tampered' WHERE entity_id = :e",
                e=ENTITIES["bank_a_field"],
            ),
        )
    )
    results.append(_attempt("delete an audit event", in_ctx("DELETE FROM audit_event")))
    results.append(_attempt("change the lifecycle rules table", in_ctx("DELETE FROM lifecycle_transition")))

    print("\nAs the schema OWNER (the most privileged app-level account):")

    def owner_rewrites_audit() -> None:
        with owner.begin() as c:
            c.execute(text("UPDATE audit_event SET actor = 'someone-else'"))

    results.append(_attempt("rewrite audit history", owner_rewrites_audit))
    with owner.begin() as c:
        broken_at = c.execute(text("SELECT gnosis_verify_audit_chain()")).scalar_one()
    print(f"  audit chain verification: {'INTACT' if broken_at is None else f'broken at seq {broken_at}'}")
    results.append(broken_at is None)

    app.dispose()
    owner.dispose()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} guards held.")
    sys.exit(0 if all(results) else 1)


class _Rollback(Exception):
    """Used to roll back an attempt that unexpectedly succeeded."""


# ------------------------------------------------------------------------------------------------ main
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    sub.add_parser("ids")
    sub.add_parser("guards")
    t = sub.add_parser("token")
    t.add_argument("--tenant", action="append", required=True, help="DEMO_A or DEMO_B; repeatable")
    t.add_argument("--sub", default="user:alice")
    t.add_argument("--shared-write", action="store_true")
    args = parser.parse_args()
    if args.cmd == "seed":
        seed()
    elif args.cmd == "ids":
        ids()
    elif args.cmd == "guards":
        guards()
    else:
        token(args.tenant, args.sub, args.shared_write)


if __name__ == "__main__":
    main()
