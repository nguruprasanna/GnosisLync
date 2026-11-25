"""Append-only audit log writer.

Hashing and chaining happen in the database trigger (db/sql/0001_core.sql)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from gnosis.context import RequestContext


def record(
    conn: Connection,
    ctx: RequestContext,
    action: str,
    *,
    tenant_id: UUID | None,
    object_ref: str | None = None,
    role: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write one audit event inside the caller's transaction (it commits or rolls back with the change)."""
    payload = {"purpose": ctx.purpose.value, **(details or {})}
    conn.execute(
        text(
            "INSERT INTO audit_event (tenant_id, actor, actor_role, action, object_ref, details, "
            "prev_hash, hash) VALUES (:t, :actor, :role, :action, :ref, CAST(:details AS jsonb), '', '')"
        ),
        {
            "t": tenant_id,
            "actor": ctx.principal.subject,
            "role": role,
            "action": action,
            "ref": object_ref,
            "details": json.dumps(payload, sort_keys=True, default=str),
        },
    )
