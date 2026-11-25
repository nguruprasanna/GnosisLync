"""RFC 9457 problem responses and the mapping from domain error codes to HTTP status."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from gnosis.context import ContextError
from gnosis.knowledge.lifecycle import LifecycleError
from gnosis.knowledge.registry import KnowledgeError

PROBLEM = "application/problem+json"

STATUS_BY_CODE: dict[str, int] = {
    "NOT_FOUND": 404,
    "TENANT_REQUIRED": 400,
    # conflicts with current state
    "BASE_VERSION_CHANGED": 409,
    "ENTITY_EXISTS": 409,
    "CHANGE_SET_NOT_OPEN": 409,
    "CHANGE_SET_NOT_IN_REVIEW": 409,
    "ITEM_NOT_REVIEWABLE": 409,
    "ALREADY_DECIDED": 409,
    # authorisation
    "SHARED_WRITE_FORBIDDEN": 403,
    "SUBMIT_FORBIDDEN": 403,
    "HUMAN_REVIEW_REQUIRED": 403,
    "ROLE_NOT_GRANTED": 403,
    "NO_REVIEW_AUTHORITY": 403,
    "SELF_REVIEW_FORBIDDEN": 403,
    "REVIEW_AUTHORITY_INSUFFICIENT": 403,
    "PURPOSE_NOT_PERMITTED": 403,
}
# PostgreSQL SQLSTATEs that reach the API when a database guard (RLS, trigger, constraint) fires.
SQLSTATE_PROBLEMS: dict[str, tuple[int, str]] = {
    "42501": (403, "FORBIDDEN_BY_POLICY"),
    "23514": (422, "INVARIANT_VIOLATION"),
    "23505": (409, "DUPLICATE"),
    "23503": (422, "REFERENCE_INVALID"),
}


class ApiProblem(Exception):
    """Raised by request handling to return an RFC 9457 problem response."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(f"{code}: {title}")
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def problem(status: int, code: str, title: str, detail: str | None = None) -> JSONResponse:
    content: dict[str, Any] = {
        "type": f"https://gnosislync.svarm/problems/{code.lower()}",
        "title": title,
        "status": status,
        "gnosis_code": code,
    }
    if detail:
        content["detail"] = detail
    return JSONResponse(status_code=status, media_type=PROBLEM, content=content)


def _message(exc: Exception) -> str:
    text = str(exc)
    code = getattr(exc, "code", "")
    return text.removeprefix(f"{code}: ") if code else text


def install(app: FastAPI) -> None:
    @app.exception_handler(ContextError)
    async def _context_error(_: Request, exc: ContextError) -> JSONResponse:
        status = 401 if exc.code == "INVALID_TOKEN" else 403
        return problem(status, exc.code, "Request context rejected")

    @app.exception_handler(ApiProblem)
    async def _api_problem(_: Request, exc: ApiProblem) -> JSONResponse:
        return problem(exc.status, exc.code, exc.title, exc.detail)

    @app.exception_handler(KnowledgeError)
    async def _knowledge_error(_: Request, exc: KnowledgeError) -> JSONResponse:
        return problem(
            STATUS_BY_CODE.get(exc.code, 422), exc.code, "Knowledge change rejected", _message(exc)
        )

    @app.exception_handler(LifecycleError)
    async def _lifecycle_error(_: Request, exc: LifecycleError) -> JSONResponse:
        return problem(422, exc.code, "Lifecycle rule violated", _message(exc))

    @app.exception_handler(DBAPIError)
    async def _db_error(_: Request, exc: DBAPIError) -> JSONResponse:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate not in SQLSTATE_PROBLEMS:
            raise exc
        status, code = SQLSTATE_PROBLEMS[sqlstate]
        first_line = str(exc.orig).splitlines()[0] if exc.orig else None
        return problem(status, code, "Rejected by a database guard", first_line)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in err.get("loc", ())) for err in exc.errors()})
        return problem(422, "VALIDATION_ERROR", "Request validation failed", f"invalid: {', '.join(fields)}")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return problem(exc.status_code, code, str(exc.detail))
