"""API-level isolation: context comes from the verified token, not from request parameters."""

import time
from collections.abc import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from gnosis.config import Settings
from gnosis_api.app import create_app
from tests.db.conftest import World

pytestmark = [pytest.mark.db, pytest.mark.security]

SECRET = "test-secret-that-is-at-least-32-characters"


def token(tenants: dict[str, str], **extra: object) -> str:
    claims = {
        "sub": "user:alice",
        "aud": "gnosislync",
        "iss": "gnosislync-dev",
        "exp": int(time.time()) + 300,
        "tenants": tenants,
        **extra,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


@pytest.fixture
def client(app_engine: Engine) -> Iterator[TestClient]:
    settings = Settings(jwt_hs256_secret=SECRET)  # type: ignore[arg-type]
    with TestClient(create_app(settings=settings, engine=app_engine)) as c:
        yield c


def _get(
    client: TestClient,
    entity: object,
    tok: str | None,
    tenant: str | None = None,
    purpose: str | None = "KNOWLEDGE_ADMIN",
) -> tuple[int, dict[str, object]]:
    headers = {}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    if tenant:
        headers["X-Gnosis-Tenant"] = tenant
    if purpose:
        headers["X-Gnosis-Purpose"] = purpose
    r = client.get(f"/v1/entities/{entity}", headers=headers)
    return r.status_code, r.json()


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_reads_own_entity(client: TestClient, world: World) -> None:
    status, body = _get(client, world.entity_a, token({world.code_a: str(world.tenant_a)}), world.code_a)
    assert status == 200
    assert body["name"] == "OPEN.BALANCE A"
    assert body["status"] == "OBSERVED"


def test_other_tenant_entity_is_indistinguishable_from_missing(client: TestClient, world: World) -> None:
    tok = token({world.code_a: str(world.tenant_a)})
    status_other, body_other = _get(client, world.entity_b, tok, world.code_a)
    status_missing, body_missing = _get(client, "00000000-0000-0000-0000-000000000000", tok, world.code_a)
    assert status_other == status_missing == 404
    assert body_other == body_missing


def test_header_cannot_grant_unentitled_tenant(client: TestClient, world: World) -> None:
    status, body = _get(client, world.entity_b, token({world.code_a: str(world.tenant_a)}), world.code_b)
    assert status == 403
    assert body["gnosis_code"] == "TENANT_FORBIDDEN"


def test_tenant_header_narrows_multi_tenant_principal(client: TestClient, world: World) -> None:
    tok = token({world.code_a: str(world.tenant_a), world.code_b: str(world.tenant_b)})
    assert _get(client, world.entity_b, tok, world.code_a)[0] == 404
    assert _get(client, world.entity_b, tok, world.code_b)[0] == 200


def test_shared_generic_knowledge_visible(client: TestClient, world: World) -> None:
    assert (
        _get(client, world.entity_generic, token({world.code_a: str(world.tenant_a)}), world.code_a)[0] == 200
    )


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not-a-jwt",
        jwt.encode(
            {"sub": "x", "aud": "gnosislync", "iss": "gnosislync-dev", "exp": int(time.time()) + 60},
            "a-different-secret-of-sufficient-length!!",
            algorithm="HS256",
        ),
        jwt.encode(
            {"sub": "x", "aud": "gnosislync", "iss": "gnosislync-dev", "exp": int(time.time()) - 60},
            SECRET,
            algorithm="HS256",
        ),
        jwt.encode(
            {"sub": "x", "aud": "other", "iss": "gnosislync-dev", "exp": int(time.time()) + 60},
            SECRET,
            algorithm="HS256",
        ),
    ],
)
def test_invalid_tokens_rejected(client: TestClient, world: World, bad: str | None) -> None:
    status, body = _get(client, world.entity_a, bad, world.code_a)
    assert status == 401
    assert body["gnosis_code"] == "INVALID_TOKEN"


def test_purpose_is_required(client: TestClient, world: World) -> None:
    status, _ = _get(
        client, world.entity_a, token({world.code_a: str(world.tenant_a)}), world.code_a, purpose=None
    )
    assert status == 400


def test_unconfigured_verifier_rejects(app_engine: Engine, world: World) -> None:
    with TestClient(create_app(settings=Settings(), engine=app_engine)) as c:
        r = c.get(
            f"/v1/entities/{world.entity_a}",
            headers={"Authorization": "Bearer x", "X-Gnosis-Purpose": "TESTING"},
        )
    assert r.status_code == 401
