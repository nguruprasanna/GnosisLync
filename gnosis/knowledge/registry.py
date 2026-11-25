"""Knowledge registry persistence: entities and immutable versions (docs/architecture/02 §5, §7).

All functions run inside the caller's ``tenant_transaction`` so row-level security applies. This module
never sets ``entity_head.verified_version``; only ``gnosis.review`` does that.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Connection, text

from gnosis.knowledge.lifecycle import Basis, Significance, Status, check_creation

Scope = Literal["GENERIC", "PRODUCT", "SITE", "SVARM_INTERNAL"]
EpistemicClass = Literal["FACT", "INTERPRETATION", "IMPLEMENTATION_SPECIFIC", "ASSUMPTION"]


class KnowledgeError(ValueError):
    """Domain error with a stable ``code`` that the API maps to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def parse_significance(value: Any) -> Significance:
    if isinstance(value, Significance):
        return value
    if isinstance(value, str):
        try:
            return Significance[value]
        except KeyError as exc:
            raise ValueError(f"unknown financial significance {value!r}") from exc
    return Significance(value)


class VersionContent(BaseModel):
    """The immutable content of one entity version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["DRAFT", "CANDIDATE", "OBSERVED"] = "CANDIDATE"
    name: str = Field(min_length=1, max_length=500)
    definition: str | None = Field(default=None, max_length=20000)
    attributes: dict[str, Any] = Field(default_factory=dict)
    release_from: int | None = None
    release_to: int | None = None
    epistemic_class: EpistemicClass = "FACT"
    verification_basis: Basis | None = None
    financial_significance: Significance = Significance.NONE
    confidence: float | None = Field(default=None, ge=0, le=1)
    sensitivity: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = "INTERNAL"

    @field_validator("financial_significance", mode="before")
    @classmethod
    def _parse_sig(cls, v: Any) -> Significance:
        return parse_significance(v)

    @model_validator(mode="after")
    def _check(self) -> VersionContent:
        rf, rt = self.release_from, self.release_to
        if rf is not None and rt is not None and rf > rt:
            raise ValueError("release_from must not exceed release_to")
        check_creation(Status(self.status), self.verification_basis)  # LifecycleError is a ValueError
        return self

    def canonical(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"status"})
        data["financial_significance"] = self.financial_significance.name
        return data

    def content_hash(self, canonical_key: str) -> bytes:
        body = {"key": canonical_key, **self.canonical()}
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).digest()


@dataclass(frozen=True)
class EntityRef:
    entity_id: UUID
    tenant_id: UUID | None
    scope: str
    kind: str
    canonical_key: str
    domain_codes: frozenset[str]
    verified_version: UUID | None
    working_version: UUID | None

    @property
    def current_version(self) -> UUID | None:
        return self.verified_version or self.working_version


def create_entity(
    conn: Connection,
    *,
    tenant_id: UUID | None,
    scope: Scope,
    kind: str,
    canonical_key: str,
    environment_id: UUID | None = None,
    product_id: UUID | None = None,
    domain_codes: frozenset[str] = frozenset(),
) -> UUID:
    allowed = conn.execute(
        text("SELECT allowed_scopes FROM entity_kind WHERE kind = :k"), {"k": kind}
    ).first()
    if allowed is None:
        raise KnowledgeError("UNKNOWN_ENTITY_KIND", f"entity kind {kind!r} is not registered")
    if scope not in allowed[0]:
        raise KnowledgeError("SCOPE_NOT_ALLOWED", f"kind {kind!r} does not allow scope {scope}")
    exists = conn.execute(
        text(
            "SELECT 1 FROM knowledge_entity WHERE tenant_id IS NOT DISTINCT FROM :t "
            "AND environment_id IS NOT DISTINCT FROM :e AND kind = :k AND canonical_key = :key"
        ),
        {"t": tenant_id, "e": environment_id, "k": kind, "key": canonical_key},
    ).first()
    if exists:
        raise KnowledgeError("ENTITY_EXISTS", f"{kind} {canonical_key!r} already exists; propose an UPDATE")
    entity_id: UUID = conn.execute(
        text(
            "INSERT INTO knowledge_entity (tenant_id, scope, kind, canonical_key, environment_id, "
            "product_id, domain_codes) VALUES (:t, :s, :k, :key, :e, :p, :d) RETURNING entity_id"
        ),
        {
            "t": tenant_id,
            "s": scope,
            "k": kind,
            "key": canonical_key,
            "e": environment_id,
            "p": product_id,
            "d": sorted(domain_codes),
        },
    ).scalar_one()
    return entity_id


_LOAD_ENTITY = (
    "SELECT e.entity_id, e.tenant_id, e.scope, e.kind, e.canonical_key, e.domain_codes, "
    "h.verified_version, h.working_version FROM knowledge_entity e "
    "LEFT JOIN entity_head h ON h.entity_id = e.entity_id WHERE e.entity_id = :id"
)
_LOAD_ENTITY_LOCKED = _LOAD_ENTITY + " FOR UPDATE OF e"


def load_entity(conn: Connection, entity_id: UUID, *, for_update: bool = False) -> EntityRef | None:
    row = conn.execute(text(_LOAD_ENTITY_LOCKED if for_update else _LOAD_ENTITY), {"id": entity_id}).first()
    if row is None:
        return None
    return EntityRef(
        entity_id=row.entity_id,
        tenant_id=row.tenant_id,
        scope=row.scope,
        kind=row.kind,
        canonical_key=row.canonical_key,
        domain_codes=frozenset(row.domain_codes),
        verified_version=row.verified_version,
        working_version=row.working_version,
    )


def add_version(
    conn: Connection,
    entity: EntityRef,
    content: VersionContent,
    *,
    produced_by: str,
    change_set_id: UUID | None,
    created_by: UUID | None,
) -> UUID:
    """Insert the next immutable version of ``entity`` and make it the working version."""
    version_no: int = conn.execute(
        text("SELECT coalesce(max(version_no), 0) + 1 FROM entity_version WHERE entity_id = :e"),
        {"e": entity.entity_id},
    ).scalar_one()
    version_id: UUID = conn.execute(
        text(
            """INSERT INTO entity_version (entity_id, tenant_id, version_no, status, name, definition,
                   attributes, release_from, release_to, epistemic_class, verification_basis,
                   financial_significance, confidence, sensitivity, produced_by, change_set_id, created_by,
                   content_hash)
               VALUES (:e, :t, :no, :status, :name, :definition, CAST(:attributes AS jsonb), :rf, :rt, :ep,
                   :basis, :sig, :conf, :sens, :pb, :cs, :cb, :hash)
               RETURNING version_id"""
        ),
        {
            "e": entity.entity_id,
            "t": entity.tenant_id,
            "no": version_no,
            "status": content.status,
            "name": content.name,
            "definition": content.definition,
            "attributes": json.dumps(content.attributes, sort_keys=True),
            "rf": content.release_from,
            "rt": content.release_to,
            "ep": content.epistemic_class,
            "basis": content.verification_basis.value if content.verification_basis else None,
            "sig": content.financial_significance.name,
            "conf": content.confidence,
            "sens": content.sensitivity,
            "pb": produced_by,
            "cs": change_set_id,
            "cb": created_by,
            "hash": content.content_hash(entity.canonical_key),
        },
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO entity_head (entity_id, tenant_id, working_version) VALUES (:e, :t, :v) "
            "ON CONFLICT (entity_id) DO UPDATE SET working_version = EXCLUDED.working_version"
        ),
        {"e": entity.entity_id, "t": entity.tenant_id, "v": version_id},
    )
    return version_id


def list_versions(conn: Connection, entity_id: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT version_id, version_no, status, name, definition, verification_basis, "
            "financial_significance, epistemic_class, release_from, release_to, produced_by, change_set_id, "
            "created_at FROM entity_version WHERE entity_id = :e ORDER BY version_no"
        ),
        {"e": entity_id},
    ).mappings()
    return [dict(r) for r in rows]
