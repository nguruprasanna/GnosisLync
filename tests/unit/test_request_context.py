import uuid

import pytest

from gnosis.context import ContextError, Principal, Purpose, RequestContext

pytestmark = [pytest.mark.unit, pytest.mark.security]

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_requested_tenant_must_be_entitled() -> None:
    p = Principal("u", frozenset({A}))
    with pytest.raises(ContextError) as e:
        RequestContext.build(p, B, Purpose.REPORTING)
    assert e.value.code == "TENANT_FORBIDDEN"


def test_requested_tenant_narrows() -> None:
    ctx = RequestContext.build(Principal("u", frozenset({A, B})), A, Purpose.REPORTING)
    assert ctx.tenant_ids == {A}


def test_no_request_uses_all_entitlements() -> None:
    ctx = RequestContext.build(Principal("u", frozenset({A, B})), None, Purpose.TESTING)
    assert ctx.tenant_ids == {A, B}


def test_db_settings() -> None:
    ctx = RequestContext.build(
        Principal("svc:ris", frozenset({B, A}), can_write_shared=True), None, Purpose.REPORTING
    )
    s = ctx.db_settings()
    assert s["gnosis.principal"] == "svc:ris"
    assert s["gnosis.tenant_ids"].split(",") == sorted([str(A), str(B)])
    assert s["gnosis.can_write_shared"] == "true"
    assert s["gnosis.purpose"] == "REPORTING"


def test_no_tenants_gives_empty_setting() -> None:
    ctx = RequestContext.build(Principal("u", frozenset()), None, Purpose.KNOWLEDGE_ADMIN)
    assert ctx.db_settings()["gnosis.tenant_ids"] == ""
    assert ctx.db_settings()["gnosis.can_write_shared"] == "false"


def test_principal_id_is_stable_and_accepts_uuid_subjects() -> None:
    import uuid as _uuid

    from gnosis.context import Principal

    a = Principal("user:alice", frozenset())
    assert a.principal_id == Principal("user:alice", frozenset()).principal_id
    assert a.principal_id != Principal("user:bob", frozenset()).principal_id
    sub = _uuid.uuid4()
    assert Principal(str(sub), frozenset()).principal_id == sub
