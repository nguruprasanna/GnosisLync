"""Local test-drive support. DEV ONLY — enabled with ``GNOSIS_DEV_MODE=true``.

Provides fixed demo personas, a token endpoint that mints HS256 tokens for them, and the browser console
at ``/studio``. ``create_app`` refuses dev mode unless the database is on localhost and an HS256 secret is
configured, so this can never be switched on against a shared environment by accident.
"""

import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import make_url

from gnosis.config import Settings
from gnosis_api.problems import ApiProblem

NS = uuid.UUID("6f1c2f0e-7a4b-4d8e-9a51-0c1d2e3f4a5b")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
STUDIO_HTML = Path(__file__).resolve().parent / "static" / "studio.html"


def demo_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NS, name)


TENANTS: dict[str, tuple[str, uuid.UUID]] = {
    "DEMO_A": ("Demo Bank A", demo_id("tenant:A")),
    "DEMO_B": ("Demo Bank B", demo_id("tenant:B")),
}


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    description: str
    sub: str
    tenants: tuple[str, ...]
    roles: tuple[str, ...] = ()
    human: bool = True
    shared_write: bool = False


PERSONAS: tuple[Persona, ...] = (
    Persona("author", "Asha — Bank A analyst", "Proposes knowledge for Bank A. No review role.",
            "user:asha", ("DEMO_A",)),
    Persona("lending1", "Lena — Lending specialist", "Reviews lending items up to HIGH significance.",
            "user:lena", ("DEMO_A",), ("lending_aa_specialist",)),
    Persona("lending2", "Luis — Lending specialist", "Second approver for HIGH items (four-eyes).",
            "user:luis", ("DEMO_A",), ("lending_aa_specialist",)),
    Persona("tech", "Tomas — T24 technical developer", "Review authority only up to MEDIUM, RUNTIME/DATA.",
            "user:tomas", ("DEMO_A",), ("t24_technical_developer",)),
    Persona("bankb", "Bea — Bank B lending specialist", "Bank B only: cannot see Bank A knowledge.",
            "user:bea", ("DEMO_B",), ("lending_aa_specialist",)),
    Persona("steward", "Sam — Svarm knowledge steward", "Both banks + shared generic knowledge.",
            "user:sam", ("DEMO_A", "DEMO_B"), ("lending_aa_specialist", "knowledge_steward"),
            shared_write=True),
    Persona("service", "RIS Sense — service account", "Machine client: may read and propose, never review.",
            "svc:ris-sense", ("DEMO_A",), ("lending_aa_specialist",), human=False),
)  # fmt: skip
BY_ID = {p.id: p for p in PERSONAS}


def check_dev_mode(settings: Settings) -> None:
    if settings.jwt_hs256_secret is None:
        raise RuntimeError("GNOSIS_DEV_MODE requires GNOSIS_JWT_HS256_SECRET")
    host = make_url(settings.database_url).host or ""
    if host not in LOCAL_HOSTS:
        raise RuntimeError(f"GNOSIS_DEV_MODE is only allowed against a local database (got host {host!r})")


def mint(settings: Settings, persona: Persona, ttl_seconds: int = 8 * 3600) -> str:
    assert settings.jwt_hs256_secret is not None  # noqa: S101 - guaranteed by check_dev_mode
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": persona.sub,
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + ttl_seconds,
        "tenants": {code: str(TENANTS[code][1]) for code in persona.tenants},
        "roles": list(persona.roles),
        "principal_type": "HUMAN" if persona.human else "SERVICE",
        "gnosis_shared_write": persona.shared_write,
    }
    return jwt.encode(claims, settings.jwt_hs256_secret.get_secret_value(), algorithm="HS256")


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: str


def router(settings: Settings) -> APIRouter:
    r = APIRouter(tags=["dev"], include_in_schema=False)

    @r.get("/dev/personas")
    def personas() -> dict[str, Any]:
        return {
            "personas": [asdict(p) for p in PERSONAS],
            "tenants": {code: name for code, (name, _) in TENANTS.items()},
        }

    @r.post("/dev/token")
    def token(body: TokenRequest) -> dict[str, str]:
        persona = BY_ID.get(body.persona)
        if persona is None:
            raise ApiProblem(404, "NOT_FOUND", "Unknown persona")
        return {"token": mint(settings, persona), "persona": persona.id}

    @r.get("/studio")
    def studio(_: Request) -> FileResponse:
        return FileResponse(STUDIO_HTML, media_type="text/html")

    return r
