import pytest

from gnosis.config import Settings
from gnosis_api import dev

pytestmark = [pytest.mark.unit, pytest.mark.security]

SECRET = "unit-test-secret-at-least-32-characters"


def test_dev_mode_requires_secret() -> None:
    with pytest.raises(RuntimeError, match="SECRET"):
        dev.check_dev_mode(Settings(dev_mode=True))


@pytest.mark.parametrize("host", ["db.internal", "10.0.0.5", "gnosis-prod.example"])
def test_dev_mode_refused_for_non_local_database(host: str) -> None:
    settings = Settings(
        dev_mode=True,
        jwt_hs256_secret=SECRET,  # type: ignore[arg-type]
        database_url=f"postgresql+psycopg://u:p@{host}:5432/gnosis",
    )
    with pytest.raises(RuntimeError, match="local database"):
        dev.check_dev_mode(settings)


def test_dev_mode_allowed_for_localhost() -> None:
    dev.check_dev_mode(Settings(dev_mode=True, jwt_hs256_secret=SECRET))  # type: ignore[arg-type]


def test_personas_are_consistent() -> None:
    ids = [p.id for p in dev.PERSONAS]
    assert len(ids) == len(set(ids))
    for p in dev.PERSONAS:
        assert set(p.tenants) <= set(dev.TENANTS)
    assert any(not p.human for p in dev.PERSONAS), "a SERVICE persona demonstrates the human-only review rule"
