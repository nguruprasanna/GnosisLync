"""Change sets and review through the API: lifecycle, four-eyes, authority, isolation and audit."""

import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from gnosis.config import Settings
from gnosis_api.app import create_app
from tests.db.conftest import World

pytestmark = [pytest.mark.db, pytest.mark.knowledge, pytest.mark.security]

SECRET = "test-secret-that-is-at-least-32-characters"


@pytest.fixture
def client(app_engine: Engine, owner_engine: Engine) -> Iterator[TestClient]:
    with owner_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO entity_kind (kind, allowed_scopes, pack) VALUES "
                "('business_rule', '{SITE,PRODUCT}', 'site_core') ON CONFLICT DO NOTHING"
            )
        )
    settings = Settings(jwt_hs256_secret=SECRET)  # type: ignore[arg-type]
    with TestClient(create_app(settings=settings, engine=app_engine)) as c:
        yield c


class Api:
    def __init__(self, client: TestClient, world: World) -> None:
        self.client = client
        self.w = world

    def token(
        self,
        sub: str,
        *,
        tenants: str = "A",
        roles: tuple[str, ...] = (),
        human: bool = True,
        shared: bool = False,
    ) -> str:
        ids = {"A": (self.w.code_a, self.w.tenant_a), "B": (self.w.code_b, self.w.tenant_b)}
        claims = {
            "sub": sub,
            "aud": "gnosislync",
            "iss": "gnosislync-dev",
            "exp": int(time.time()) + 300,
            "tenants": {ids[t][0]: str(ids[t][1]) for t in tenants},
            "roles": list(roles),
            "principal_type": "HUMAN" if human else "SERVICE",
            "gnosis_shared_write": shared,
        }
        return jwt.encode(claims, SECRET, algorithm="HS256")

    def call(
        self,
        method: str,
        path: str,
        tok: str,
        *,
        tenant: str | None = "A",
        purpose: str = "KNOWLEDGE_ADMIN",
        role: str | None = None,
        body: Any = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {tok}", "X-Gnosis-Purpose": purpose}
        if tenant:
            headers["X-Gnosis-Tenant"] = self.w.code_a if tenant == "A" else self.w.code_b
        if role:
            headers["X-Gnosis-Role"] = role
        response: httpx.Response = self.client.request(method, path, headers=headers, json=body)
        return response


AUTHOR = "user:author"
LENDING_1 = "user:lending-1"
LENDING_2 = "user:lending-2"
TECH = "user:tech"


@pytest.fixture
def api(client: TestClient, world: World) -> Api:
    return Api(client, world)


def rule_item(world: World, *, significance: str = "HIGH", materiality: str = "FINANCIAL") -> dict[str, Any]:
    return {
        "op": "CREATE",
        "scope": "SITE",
        "kind": "business_rule",
        "canonical_key": f"site:rule:principal-balance-types:{uuid.uuid4().hex[:8]}",
        "environment_id": str(world.env_a),
        "domain_codes": ["LENDING"],
        "materiality": materiality,
        "version": {
            "status": "CANDIDATE",
            "name": "Principal balance types",
            "definition": "CURACCOUNT + DUEACCOUNT + overdue variants",
            "epistemic_class": "INTERPRETATION",
            "verification_basis": "AI_INFERRED",
            "financial_significance": significance,
        },
    }


def propose(api: Api, items: list[dict[str, Any]], *, target: str = "TENANT", **kw: Any) -> httpx.Response:
    tok = kw.pop("tok", None) or api.token(AUTHOR)
    return api.call(
        "POST", "/v1/change-sets", tok, body={"title": "t", "target": target, "items": items}, **kw
    )


def submitted(api: Api, items: list[dict[str, Any]]) -> dict[str, Any]:
    r = propose(api, items)
    assert r.status_code == 201, r.text
    cs = r.json()
    r = api.call("POST", f"/v1/change-sets/{cs['change_set_id']}:submit", api.token(AUTHOR))
    assert r.status_code == 200, r.text
    result: dict[str, Any] = r.json()
    return result


def decide(
    api: Api,
    cs: dict[str, Any],
    who: str,
    decision: str,
    *,
    role: str = "lending_aa_specialist",
    roles: tuple[str, ...] | None = None,
    human: bool = True,
    item: int = 0,
) -> httpx.Response:
    tok = api.token(who, roles=roles if roles is not None else (role,), human=human)
    body = {
        "decisions": [
            {
                "change_item_id": cs["items"][item]["change_item_id"],
                "decision": decision,
                "rationale": "checked against AA product conditions",
            }
        ]
    }
    return api.call("POST", f"/v1/change-sets/{cs['change_set_id']}/decisions", tok, role=role, body=body)


# ------------------------------------------------------------------------------------------- happy paths
def test_high_significance_needs_two_distinct_qualified_approvers(api: Api, app_engine: Engine) -> None:
    cs = submitted(api, [rule_item(api.w)])
    assert cs["status"] == "IN_REVIEW"
    assert cs["items"][0]["status"] == "UNDER_REVIEW"

    r = decide(api, cs, LENDING_1, "APPROVE")
    assert r.status_code == 200, r.text
    first = r.json()["results"][0]
    assert (first["status"], first["approvals_present"], first["approvals_required"]) == (
        "UNDER_REVIEW",
        1,
        2,
    )
    assert r.json()["change_set"]["status"] == "IN_REVIEW"

    assert decide(api, cs, LENDING_1, "APPROVE").json()["gnosis_code"] == "ALREADY_DECIDED"

    r = decide(api, cs, LENDING_2, "APPROVE")
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] == "VERIFIED"
    final = r.json()["change_set"]
    assert final["status"] == "APPROVED"
    assert final["items"][0]["verification_basis"] == "SPECIALIST_APPROVED"
    assert len(final["items"][0]["decisions"]) == 2

    entity = api.call("GET", f"/v1/entities/{final['items'][0]['entity_id']}", api.token(AUTHOR)).json()
    assert entity["status"] == "VERIFIED"


def test_observed_low_significance_metadata_is_auto_verified_on_submit(api: Api) -> None:
    item = {
        "op": "CREATE",
        "scope": "SITE",
        "kind": "site_field",
        "canonical_key": f"site:t24:LD>RATE:{uuid.uuid4().hex[:8]}",
        "environment_id": str(api.w.env_a),
        "materiality": "DESCRIPTIVE",
        "version": {"status": "OBSERVED", "name": "RATE", "verification_basis": "SOURCE_METADATA"},
    }
    cs = submitted(api, [item])
    assert cs["status"] == "APPROVED"
    assert cs["items"][0]["status"] == "VERIFIED"
    assert cs["items"][0]["decisions"] == []


def test_update_supersedes_previous_verified_version(api: Api) -> None:
    cs = submitted(api, [rule_item(api.w, significance="LOW", materiality="SEMANTIC")])
    decide(api, cs, LENDING_1, "APPROVE")
    entity_id = cs["items"][0]["entity_id"]
    v1 = cs["items"][0]["version_id"]

    update = {
        "op": "UPDATE",
        "entity_id": entity_id,
        "base_version_id": v1,
        "materiality": "DESCRIPTIVE",
        "version": {
            "name": "Principal balance types",
            "definition": "clarified wording",
            "epistemic_class": "INTERPRETATION",
            "verification_basis": "DOCUMENTATION",
            "financial_significance": "LOW",
        },
    }
    cs2 = submitted(api, [update])
    assert cs2["items"][0]["diff"]["changed_fields"] == ["definition", "verification_basis"]
    r = decide(api, cs2, LENDING_1, "APPROVE")
    assert r.json()["results"][0]["status"] == "VERIFIED"

    versions = api.call("GET", f"/v1/entities/{entity_id}/versions", api.token(AUTHOR)).json()["versions"]
    assert [(v["version_no"], v["status"]) for v in versions] == [(1, "SUPERSEDED"), (2, "VERIFIED")]
    assert versions[1]["verification_basis"] == "DOCUMENTATION"

    stale = propose(api, [{**update, "base_version_id": v1}])
    assert stale.status_code == 409
    assert stale.json()["gnosis_code"] == "BASE_VERSION_CHANGED"


def test_competing_updates_second_approval_conflicts(api: Api) -> None:
    cs = submitted(api, [rule_item(api.w, significance="LOW", materiality="SEMANTIC")])
    decide(api, cs, LENDING_1, "APPROVE")
    base = cs["items"][0]["version_id"]

    def update(text_: str) -> dict[str, Any]:
        return {
            "op": "UPDATE",
            "entity_id": cs["items"][0]["entity_id"],
            "base_version_id": base,
            "materiality": "DESCRIPTIVE",
            "version": {
                "name": "Principal balance types",
                "definition": text_,
                "verification_basis": "DOCUMENTATION",
                "financial_significance": "LOW",
            },
        }

    first = submitted(api, [update("one")])
    # The second proposal was made against the same base before the first was verified.
    second_resp = propose(api, [update("two")])
    assert second_resp.status_code == 201
    second = second_resp.json()
    api.call("POST", f"/v1/change-sets/{second['change_set_id']}:submit", api.token(AUTHOR))
    second = api.call("GET", f"/v1/change-sets/{second['change_set_id']}", api.token(AUTHOR)).json()

    assert decide(api, first, LENDING_1, "APPROVE").status_code == 200
    r = decide(api, second, LENDING_1, "APPROVE")
    assert r.status_code == 409
    assert r.json()["gnosis_code"] == "BASE_VERSION_CHANGED"


@pytest.mark.parametrize(
    ("decision", "item_status", "cs_status"),
    [
        ("REJECT", "REJECTED", "REJECTED"),
        ("REQUEST_CHANGES", "CANDIDATE", "REJECTED"),
        ("APPROVE_PROVISIONAL", "PROVISIONAL", "APPROVED"),
    ],
)
def test_other_decisions(api: Api, decision: str, item_status: str, cs_status: str) -> None:
    cs = submitted(api, [rule_item(api.w)])
    r = decide(api, cs, LENDING_1, decision)
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] == item_status
    assert r.json()["change_set"]["status"] == cs_status


# ------------------------------------------------------------------------------------------- refusals
@pytest.mark.parametrize(
    ("who", "kw", "code", "status"),
    [
        (AUTHOR, {}, "SELF_REVIEW_FORBIDDEN", 403),
        (LENDING_1, {"human": False}, "HUMAN_REVIEW_REQUIRED", 403),
        (LENDING_1, {"roles": ()}, "ROLE_NOT_GRANTED", 403),
        (TECH, {"role": "t24_technical_developer"}, "REVIEW_AUTHORITY_INSUFFICIENT", 403),
        (LENDING_1, {"role": "no_such_role", "roles": ("no_such_role",)}, "NO_REVIEW_AUTHORITY", 403),
    ],
)
def test_review_refusals(api: Api, who: str, kw: dict[str, Any], code: str, status: int) -> None:
    cs = submitted(api, [rule_item(api.w)])
    r = decide(api, cs, who, "APPROVE", **kw)
    assert r.status_code == status, r.text
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["gnosis_code"] == code


def test_bulk_approval_refused_for_financial_items(api: Api) -> None:
    cs = submitted(api, [rule_item(api.w), rule_item(api.w, significance="LOW", materiality="SEMANTIC")])
    body = {
        "decisions": [
            {"change_item_id": i["change_item_id"], "decision": "APPROVE", "rationale": "ok"}
            for i in cs["items"]
        ]
    }
    r = api.call(
        "POST",
        f"/v1/change-sets/{cs['change_set_id']}/decisions",
        api.token(LENDING_1, roles=("lending_aa_specialist",)),
        role="lending_aa_specialist",
        body=body,
    )
    assert r.status_code == 422
    assert r.json()["gnosis_code"] == "BULK_APPROVAL_FORBIDDEN"


def test_cannot_decide_before_submit_or_submit_twice(api: Api) -> None:
    cs = propose(api, [rule_item(api.w)]).json()
    r = decide(api, cs, LENDING_1, "APPROVE")
    assert r.status_code == 409
    assert r.json()["gnosis_code"] == "CHANGE_SET_NOT_IN_REVIEW"
    other = api.call("POST", f"/v1/change-sets/{cs['change_set_id']}:submit", api.token(LENDING_1))
    assert other.json()["gnosis_code"] == "SUBMIT_FORBIDDEN"
    path = f"/v1/change-sets/{cs['change_set_id']}:submit"
    assert api.call("POST", path, api.token(AUTHOR)).status_code == 200
    assert api.call("POST", path, api.token(AUTHOR)).json()["gnosis_code"] == "CHANGE_SET_NOT_OPEN"


def test_writes_require_knowledge_admin_purpose(api: Api) -> None:
    r = propose(api, [rule_item(api.w)], purpose="REPORTING")
    assert r.status_code == 403
    assert r.json()["gnosis_code"] == "PURPOSE_NOT_PERMITTED"


def test_shared_target_requires_shared_write(api: Api) -> None:
    generic = {
        "op": "CREATE",
        "scope": "GENERIC",
        "kind": "banking_concept",
        "canonical_key": f"concept:x:{uuid.uuid4().hex[:6]}",
        "materiality": "SEMANTIC",
        "version": {"name": "X"},
    }
    assert (
        propose(api, [generic], target="SHARED", tenant=None).json()["gnosis_code"]
        == "SHARED_WRITE_FORBIDDEN"
    )
    assert propose(api, [generic]).json()["gnosis_code"] == "SCOPE_TARGET_MISMATCH"
    ok = propose(api, [generic], target="SHARED", tenant=None, tok=api.token(AUTHOR, shared=True))
    assert ok.status_code == 201, ok.text
    assert ok.json()["tenant_id"] is None


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"kind": "no_such_kind"}, "UNKNOWN_ENTITY_KIND"),
        ({"scope": "PRODUCT", "environment_id": None, "kind": "site_field"}, "SCOPE_NOT_ALLOWED"),
        (
            {"version": {"status": "OBSERVED", "name": "x", "verification_basis": "AI_INFERRED"}},
            "VALIDATION_ERROR",
        ),
        ({"version": {"status": "VERIFIED", "name": "x"}}, "VALIDATION_ERROR"),
    ],
)
def test_invalid_proposals(api: Api, change: dict[str, Any], code: str) -> None:
    r = propose(api, [{**rule_item(api.w), **change}])
    assert r.status_code == 422, r.text
    assert r.json()["gnosis_code"] == code


def test_duplicate_entity_refused(api: Api) -> None:
    item = rule_item(api.w)
    assert propose(api, [item]).status_code == 201
    assert propose(api, [item]).json()["gnosis_code"] == "ENTITY_EXISTS"


# ----------------------------------------------------------------------------------- isolation & audit
def test_other_tenant_cannot_see_or_review(api: Api) -> None:
    cs = submitted(api, [rule_item(api.w)])
    tok_b = api.token(LENDING_1, tenants="B", roles=("lending_aa_specialist",))
    path = f"/v1/change-sets/{cs['change_set_id']}"
    assert api.call("GET", path, tok_b, tenant="B").status_code == 404
    listed = api.call("GET", "/v1/change-sets", tok_b, tenant="B").json()["change_sets"]
    assert cs["change_set_id"] not in {c["change_set_id"] for c in listed}
    body = {
        "decisions": [
            {"change_item_id": cs["items"][0]["change_item_id"], "decision": "APPROVE", "rationale": "x"}
        ]
    }
    r = api.call("POST", f"{path}/decisions", tok_b, tenant="B", role="lending_aa_specialist", body=body)
    assert r.status_code == 404


def test_decisions_are_signed_and_audited(api: Api, owner_engine: Engine) -> None:
    cs = submitted(api, [rule_item(api.w, significance="LOW", materiality="SEMANTIC")])
    decide(api, cs, LENDING_1, "APPROVE")
    with owner_engine.begin() as c:
        sig = c.execute(
            text("SELECT length(signed_hash) FROM review_decision WHERE change_set_id = :cs"),
            {"cs": cs["change_set_id"]},
        ).scalar_one()
        actions = (
            c.execute(
                text("SELECT action FROM audit_event WHERE object_ref IN (:a, :b) ORDER BY seq"),
                {
                    "a": f"change_set:{cs['change_set_id']}",
                    "b": f"change_item:{cs['items'][0]['change_item_id']}",
                },
            )
            .scalars()
            .all()
        )
        broken = c.execute(text("SELECT gnosis_verify_audit_chain()")).scalar_one()
    assert sig == 32
    assert actions == ["change_set.proposed", "change_set.submitted", "review.decision"]
    assert broken is None


# ------------------------------------------------------------------------------------------- edge cases
def test_partially_approved_and_rejected_item_not_reviewable_again(api: Api) -> None:
    low = rule_item(api.w, significance="LOW", materiality="SEMANTIC")
    cs = submitted(api, [rule_item(api.w), low])
    high_idx = next(i for i, it in enumerate(cs["items"]) if it["financial_significance"] == "HIGH")
    low_idx = 1 - high_idx
    assert decide(api, cs, LENDING_1, "REJECT", item=high_idx).json()["change_set"]["status"] == "IN_REVIEW"
    r = decide(api, cs, LENDING_2, "APPROVE", item=high_idx)
    assert r.status_code == 409
    assert r.json()["gnosis_code"] == "ITEM_NOT_REVIEWABLE"
    assert decide(api, cs, LENDING_1, "APPROVE", item=low_idx).json()["change_set"]["status"] == (
        "PARTIALLY_APPROVED"
    )


def test_request_shape_refusals(api: Api) -> None:
    cs = submitted(api, [rule_item(api.w)])
    tok = api.token(LENDING_1, roles=("lending_aa_specialist",))
    path = f"/v1/change-sets/{cs['change_set_id']}/decisions"
    item = cs["items"][0]["change_item_id"]
    dup = {"decisions": [{"change_item_id": item, "decision": "REJECT", "rationale": "x"}] * 2}
    r = api.call("POST", path, tok, role="lending_aa_specialist", body=dup)
    assert r.json()["gnosis_code"] == "DUPLICATE_ITEM"
    unknown = {"decisions": [{"change_item_id": str(uuid.uuid4()), "decision": "REJECT", "rationale": "x"}]}
    r = api.call("POST", path, tok, role="lending_aa_specialist", body=unknown)
    assert (r.status_code, r.json()["gnosis_code"]) == (404, "NOT_FOUND")
    empty_rationale = {"decisions": [{"change_item_id": item, "decision": "REJECT", "rationale": ""}]}
    assert api.call("POST", path, tok, role="lending_aa_specialist", body=empty_rationale).status_code == 422
    assert (
        api.call("POST", path, tok, body={"decisions": []}, role="lending_aa_specialist").status_code == 422
    )


def test_target_and_entity_refusals(api: Api) -> None:
    both = api.token(AUTHOR, tenants="AB")
    r = propose(api, [rule_item(api.w)], tok=both, tenant=None)
    assert (r.status_code, r.json()["gnosis_code"]) == (400, "TENANT_REQUIRED")
    r = propose(api, [rule_item(api.w)], target="SHARED", tenant=None, tok=api.token(AUTHOR, shared=True))
    assert r.json()["gnosis_code"] == "SCOPE_TARGET_MISMATCH"
    missing = {
        "op": "UPDATE",
        "entity_id": str(uuid.uuid4()),
        "base_version_id": str(uuid.uuid4()),
        "materiality": "DESCRIPTIVE",
        "version": {"name": "x"},
    }
    r = propose(api, [missing])
    assert (r.status_code, r.json()["gnosis_code"]) == (404, "NOT_FOUND")
    other_tenant = {**missing, "entity_id": str(api.w.entity_b)}
    assert propose(api, [other_tenant]).status_code == 404


def test_shared_change_set_review_requires_shared_write(api: Api) -> None:
    generic = {
        "op": "CREATE",
        "scope": "GENERIC",
        "kind": "banking_concept",
        "domain_codes": ["LENDING"],
        "canonical_key": f"concept:y:{uuid.uuid4().hex[:6]}",
        "materiality": "SEMANTIC",
        "version": {"name": "Y", "financial_significance": "LOW"},
    }
    author = api.token(AUTHOR, shared=True)
    cs = propose(api, [generic], target="SHARED", tenant=None, tok=author).json()
    api.call("POST", f"/v1/change-sets/{cs['change_set_id']}:submit", author, tenant=None)
    cs = api.call("GET", f"/v1/change-sets/{cs['change_set_id']}", author, tenant=None).json()
    body = {
        "decisions": [
            {"change_item_id": cs["items"][0]["change_item_id"], "decision": "APPROVE", "rationale": "x"}
        ]
    }
    path = f"/v1/change-sets/{cs['change_set_id']}/decisions"
    plain = api.token(LENDING_1, roles=("lending_aa_specialist",))
    r = api.call("POST", path, plain, tenant=None, role="lending_aa_specialist", body=body)
    assert r.json()["gnosis_code"] == "SHARED_WRITE_FORBIDDEN"
    svarm = api.token(LENDING_1, roles=("lending_aa_specialist",), shared=True)
    r = api.call("POST", path, svarm, tenant=None, role="lending_aa_specialist", body=body)
    assert r.json()["results"][0]["status"] == "VERIFIED"


def test_versions_of_unknown_entity_is_404(api: Api) -> None:
    r = api.call("GET", f"/v1/entities/{uuid.uuid4()}/versions", api.token(AUTHOR))
    assert r.status_code == 404


def test_service_rejects_empty_decisions_and_ignores_retired_roles(api: Api, app_engine: Engine) -> None:
    from gnosis.context import Principal, PrincipalKind, Purpose, RequestContext
    from gnosis.context.db import tenant_transaction
    from gnosis.knowledge.lifecycle import Decision
    from gnosis.knowledge.registry import KnowledgeError
    from gnosis.review import service
    from gnosis.roles import RoleRegistry

    cs = submitted(api, [rule_item(api.w)])
    cs_id = uuid.UUID(cs["change_set_id"])
    item_id = uuid.UUID(cs["items"][0]["change_item_id"])
    lending = api.client.app.state.roles.get("lending_aa_specialist")  # type: ignore[attr-defined]
    temp = lending.model_copy(update={"id": "temp_lender"})
    with_temp = RoleRegistry({"lending_aa_specialist": lending, "temp_lender": temp})
    without_temp = RoleRegistry({"lending_aa_specialist": lending})

    def ctx(sub: str, role: str) -> RequestContext:
        p = Principal(sub, frozenset({api.w.tenant_a}), roles=frozenset({role}), kind=PrincipalKind.HUMAN)
        return RequestContext.build(p, None, Purpose.KNOWLEDGE_ADMIN)

    approve = [service.DecisionInput(change_item_id=item_id, decision=Decision.APPROVE, rationale="ok")]
    first = ctx(LENDING_1, "temp_lender")
    with tenant_transaction(app_engine, first) as c, pytest.raises(KnowledgeError, match="NO_DECISIONS"):
        service.decide(c, first, cs_id, [], role="temp_lender", roles=with_temp)
    with tenant_transaction(app_engine, first) as c:
        service.decide(c, first, cs_id, approve, role="temp_lender", roles=with_temp)

    # temp_lender is retired before the second approval: the first approval no longer counts (four-eyes).
    second = ctx(LENDING_2, "lending_aa_specialist")
    with tenant_transaction(app_engine, second) as c:
        results = service.decide(c, second, cs_id, approve, role="lending_aa_specialist", roles=without_temp)
    assert results[0].status.value == "UNDER_REVIEW"
    assert results[0].approvals_present == 1
