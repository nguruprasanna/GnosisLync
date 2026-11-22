"""Alembic environment. Migrations run as the schema-owner role, never as the application role."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("GNOSIS_MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("Set GNOSIS_MIGRATION_DATABASE_URL (schema-owner connection URL)")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) mode is not supported; migrations execute raw SQL files.")
run_migrations_online()
