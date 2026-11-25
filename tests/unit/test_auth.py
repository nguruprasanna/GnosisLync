import time
import uuid

import jwt
import pytest

from gnosis.context import ContextError
from gnosis_api.auth import HS256DevVerifier

pytestmark = [pytest.mark.unit, pytest.mark.security]

SECRET = "unit-test-secret-at-least-32-characters"
V = HS256DevVerifier(SECRET, "gnosislync", "gnosislync-dev")


def enc(claims: dict[str, object], key: str = SECRET, alg: str = "HS256") -> str:
    base: dict[str, object] = {
        "sub": "u",
        "aud": "gnosislync",
        "iss": "gnosislync-dev",
        "exp": int(time.time()) + 60,
    }
    return jwt.encode({**base, **claims}, key, algorithm=alg)


def test_valid_token() -> None:
    t = uuid.uuid4()
    principal, codes = V.verify(enc({"tenants": {"NLB": str(t)}, "gnosis_shared_write": True}))
    assert principal.tenant_ids == {t}
    assert codes == {"NLB": t}
    assert principal.can_write_shared


def test_shared_write_must_be_boolean_true() -> None:
    principal, _ = V.verify(enc({"gnosis_shared_write": "true"}))
    assert not principal.can_write_shared


@pytest.mark.parametrize("claims", [{"tenants": ["x"]}, {"tenants": {"NLB": "not-a-uuid"}}])
def test_malformed_tenants(claims: dict[str, object]) -> None:
    with pytest.raises(ContextError):
        V.verify(enc(claims))


def test_missing_exp_rejected() -> None:
    tok = jwt.encode({"sub": "u", "aud": "gnosislync", "iss": "gnosislync-dev"}, SECRET, algorithm="HS256")
    with pytest.raises(ContextError):
        V.verify(tok)


def test_alg_none_rejected() -> None:
    tok = jwt.encode(
        {"sub": "u", "aud": "gnosislync", "iss": "gnosislync-dev", "exp": int(time.time()) + 60},
        None,
        algorithm="none",
    )  # type: ignore[arg-type]
    with pytest.raises(ContextError):
        V.verify(tok)


def test_wrong_issuer_rejected() -> None:
    with pytest.raises(ContextError):
        V.verify(enc({"iss": "evil"}))


def test_short_secret_refused() -> None:
    with pytest.raises(ValueError):
        HS256DevVerifier("short", "a", "b")


def test_roles_and_principal_type() -> None:
    principal, _ = V.verify(enc({"roles": ["lending_aa_specialist"], "principal_type": "HUMAN"}))
    assert principal.roles == {"lending_aa_specialist"}
    assert principal.kind.value == "HUMAN"


def test_principal_type_defaults_to_service() -> None:
    principal, _ = V.verify(enc({}))
    assert principal.kind.value == "SERVICE"
    assert principal.roles == frozenset()


@pytest.mark.parametrize("claims", [{"roles": "admin"}, {"roles": [1]}, {"principal_type": "ROBOT"}])
def test_malformed_roles_or_type(claims: dict[str, object]) -> None:
    with pytest.raises(ContextError):
        V.verify(enc(claims))
