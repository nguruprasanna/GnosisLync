"""FastAPI application factory."""

from fastapi import FastAPI
from sqlalchemy import Engine

from gnosis.config import Settings
from gnosis.context.db import make_engine
from gnosis.roles import RoleRegistry
from gnosis_api import dev, problems
from gnosis_api.auth import HS256DevVerifier, TokenVerifier
from gnosis_api.routers import audit, change_sets, entities, topology


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    verifier: TokenVerifier | None = None,
    roles: RoleRegistry | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if settings.dev_mode:
        dev.check_dev_mode(settings)
    if verifier is None and settings.jwt_hs256_secret is not None:
        verifier = HS256DevVerifier(
            settings.jwt_hs256_secret.get_secret_value(), settings.jwt_audience, settings.jwt_issuer
        )

    app = FastAPI(title="GnosisLync Intelligence API", version="0.1.0")
    app.state.engine = engine or make_engine(settings.database_url)
    app.state.verifier = verifier
    app.state.roles = roles or RoleRegistry.load(settings.roles_dir)
    problems.install(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(entities.router)
    app.include_router(change_sets.router)
    app.include_router(topology.router)
    app.include_router(audit.router)
    if settings.dev_mode:
        app.include_router(dev.router(settings))
    return app
