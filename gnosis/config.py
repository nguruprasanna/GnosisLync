"""Runtime settings (environment variables prefixed GNOSIS_)."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GNOSIS_")

    # Application role connection (member of gnosis_app; never the schema owner).
    database_url: str = "postgresql+psycopg://gnosis_api:dev-only@localhost:55432/gnosis"
    # Phase 0 token verification: HS256 shared secret for development/testing only.
    # Phase 1 replaces this with OIDC/JWKS verification against the Svarm IdP.
    jwt_hs256_secret: SecretStr | None = None
    jwt_audience: str = "gnosislync"
    jwt_issuer: str = "gnosislync-dev"
    # Versioned role profiles (docs/architecture/04 §2).
    roles_dir: Path = Path(__file__).resolve().parents[1] / "roles"
