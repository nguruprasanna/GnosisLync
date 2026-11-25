import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from gnosis.knowledge.lifecycle import LifecycleError
from gnosis_api import problems

pytestmark = [pytest.mark.unit]


def _app() -> TestClient:
    app = FastAPI()
    problems.install(app)

    @app.get("/lifecycle")
    def lifecycle() -> None:
        raise LifecycleError("ILLEGAL_TRANSITION", "CANDIDATE -> VERIFIED")

    @app.get("/db/{state}")
    def db(state: str) -> None:
        raise DBAPIError("stmt", {}, _Orig(state))

    return TestClient(app, raise_server_exceptions=False)


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"guard fired ({sqlstate})\nDETAIL: more")
        self.sqlstate = sqlstate


def test_lifecycle_error_is_problem_json() -> None:
    r = _app().get("/lifecycle")
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["gnosis_code"] == "ILLEGAL_TRANSITION"
    assert r.json()["detail"] == "CANDIDATE -> VERIFIED"


@pytest.mark.parametrize(("state", "status", "code"), [
    ("42501", 403, "FORBIDDEN_BY_POLICY"),
    ("23514", 422, "INVARIANT_VIOLATION"),
    ("23505", 409, "DUPLICATE"),
    ("23503", 422, "REFERENCE_INVALID"),
])  # fmt: skip
def test_database_guards_map_to_problems(state: str, status: int, code: str) -> None:
    r = _app().get(f"/db/{state}")
    assert r.status_code == status
    assert r.json()["gnosis_code"] == code
    assert r.json()["detail"] == f"guard fired ({state})"


def test_unknown_database_errors_are_not_masked() -> None:
    assert _app().get("/db/XX000").status_code == 500
