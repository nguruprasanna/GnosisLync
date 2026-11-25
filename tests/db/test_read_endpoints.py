"""Read endpoints used by the Studio preview, and the dev-mode test-drive surface."""

import time
from collections.abc import Iterator

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from gnosis.config import Settings
from gnosis_api.app import create_app
from tests.db.conftest import World

pytestmark = [pytest.mark.db, pytest.mark.security]

SECRET = "test-secret-that-is-at-least-32-characters"


def token(world: World, which: str = "A") -> str:
    code, tid = (world.code_a, world.tenant_a) if which == "A" else (world.code_b, world.tenant_b)
    claims = {"sub": "user:reader", "aud": "gnosislync", "iss": "gnosislync-dev",
              "exp": int(time.time()) + 300, "tenants": {code: str(tid)}}  # fmt: skip
    return jwt.encode(claims, SECRET, algorithm="HS256")


def get(client: TestClient, path: str, tok: str, purpose: str = "KNOWLEDGE_ADMIN") -> httpx.Response:
    response: httpx.Response = client.get(
        path, headers={"Authorization": f"Bearer {tok}", "X-Gnosis-Purpose": purpose}
    )
    return response


@pytest.fixture
def client(app_engine: Engine) -> Iterator[TestClient]:
    settings = Settings(jwt_hs256_secret=SECRET)  # type: ignore[arg-type]
    with TestClient(create_app(settings=settings, engine=app_engine)) as c:
        yield c


def test_entity_list_respects_tenancy_and_filters(client: TestClient, world: World) -> None:
    rows = get(client, "/v1/entities?limit=200", token(world)).json()["entities"]
    ids = {r["entity_id"] for r in rows}
    assert str(world.entity_a) in ids
    assert str(world.entity_generic) in ids
    assert str(world.entity_b) not in ids
    by_kind = get(client, "/v1/entities?kind=banking_concept&limit=200", token(world)).json()["entities"]
    assert {r["kind"] for r in by_kind} == {"banking_concept"}
    found = {
        r["entity_id"]
        for r in get(client, f"/v1/entities?q={world.code_a[1:]}", token(world)).json()["entities"]
    }
    assert str(world.entity_a) in found
    assert str(world.entity_b) not in found


def test_environments_respect_tenancy(client: TestClient, world: World) -> None:
    envs = get(client, "/v1/environments", token(world)).json()["environments"]
    assert {e["environment_id"] for e in envs} == {str(world.env_a)}
    assert envs[0]["release_code"] == "R22"


def test_audit_events_scoped_and_admin_only(client: TestClient, world: World, owner_engine: Engine) -> None:
    with owner_engine.begin() as c:
        for t, a in ((world.tenant_a, "read.test.a"), (world.tenant_b, "read.test.b")):
            c.execute(
                text(
                    "INSERT INTO audit_event (tenant_id, actor, action, object_ref, details, "
                    "prev_hash, hash) VALUES (:t, 'user:x', :a, :ref, '{}', '', '')"
                ),
                {"t": t, "a": a, "ref": f"test:{world.code_a}"},
            )
    assert get(client, "/v1/audit-events", token(world), purpose="REPORTING").status_code == 403
    events = get(client, f"/v1/audit-events?object_ref=test:{world.code_a}", token(world)).json()["events"]
    assert [e["action"] for e in events] == ["read.test.a"]


def test_dev_surface_absent_by_default(client: TestClient) -> None:
    assert client.get("/dev/personas").status_code == 404
    assert client.post("/dev/token", json={"persona": "author"}).status_code == 404
    assert client.get("/studio").status_code == 404


def test_dev_mode_personas_tokens_and_studio(app_engine: Engine) -> None:
    settings = Settings(jwt_hs256_secret=SECRET, dev_mode=True)  # type: ignore[arg-type]
    with TestClient(create_app(settings=settings, engine=app_engine)) as c:
        personas = c.get("/dev/personas").json()["personas"]
        assert {"author", "lending1", "lending2", "service"} <= {p["id"] for p in personas}
        tok = c.post("/dev/token", json={"persona": "service"}).json()["token"]
        claims = jwt.decode(tok, SECRET, algorithms=["HS256"], audience="gnosislync")
        assert claims["principal_type"] == "SERVICE"
        assert c.post("/dev/token", json={"persona": "root"}).status_code == 404
        studio = c.get("/studio")
        assert studio.status_code == 200
        assert "Studio preview" in studio.text
        assert "innerHTML" not in studio.text
