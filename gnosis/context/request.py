"""Request context derived from the authenticated principal (docs/architecture/05-api-contracts.md §1).

The effective tenant set is the intersection of what the caller asks for and what the verified token
entitles them to. Request parameters alone never grant access.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Purpose(StrEnum):
    REPORTING = "REPORTING"
    TESTING = "TESTING"
    KNOWLEDGE_ADMIN = "KNOWLEDGE_ADMIN"
    DISCOVERY = "DISCOVERY"


class ContextError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Principal:
    """Identity established by token verification."""

    subject: str
    tenant_ids: frozenset[UUID]
    can_write_shared: bool = False


@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    tenant_ids: frozenset[UUID]
    purpose: Purpose
    can_write_shared: bool

    @classmethod
    def build(
        cls,
        principal: Principal,
        requested_tenant: UUID | None,
        purpose: Purpose,
    ) -> RequestContext:
        if requested_tenant is not None:
            if requested_tenant not in principal.tenant_ids:
                raise ContextError("TENANT_FORBIDDEN", "principal is not entitled to the requested tenant")
            tenants = frozenset({requested_tenant})
        else:
            tenants = principal.tenant_ids
        return cls(
            principal=principal,
            tenant_ids=tenants,
            purpose=purpose,
            can_write_shared=principal.can_write_shared,
        )

    def db_settings(self) -> dict[str, str]:
        """Transaction-local settings read by the RLS policies."""
        return {
            "gnosis.principal": self.principal.subject,
            "gnosis.tenant_ids": ",".join(sorted(str(t) for t in self.tenant_ids)),
            "gnosis.can_write_shared": "true" if self.can_write_shared else "false",
            "gnosis.purpose": self.purpose.value,
        }
