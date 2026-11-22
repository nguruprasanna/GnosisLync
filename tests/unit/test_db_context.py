import pytest

from gnosis.context.db import make_engine

pytestmark = pytest.mark.unit


def test_make_engine_is_lazy_and_pre_pings() -> None:
    engine = make_engine("postgresql+psycopg://user:pw@127.0.0.1:1/none")  # no connection attempted
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]
    assert engine.url.username == "user"
    engine.dispose()
