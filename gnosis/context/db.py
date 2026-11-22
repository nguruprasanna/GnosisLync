"""Database sessions bound to a request context.

Every application transaction must go through :func:`tenant_transaction`. Settings are applied with
``set_config(..., is_local => true)`` so they vanish at COMMIT/ROLLBACK and cannot leak to the next user
of a pooled connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text

from gnosis.context.request import RequestContext


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


@contextmanager
def tenant_transaction(engine: Engine, ctx: RequestContext) -> Iterator[Connection]:
    with engine.begin() as conn:
        for key, value in ctx.db_settings().items():
            conn.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": value})
        yield conn
