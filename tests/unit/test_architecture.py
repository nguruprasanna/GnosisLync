"""Static architecture rules that import-linter cannot express."""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[2]
# SQL that sets the verified head (UPDATE ... SET, or INSERT INTO entity_head with the column).
VERIFIED_WRITE = re.compile(
    r"SET\s+verified_version\s*=|INSERT\s+INTO\s+entity_head\s*\([^)]*verified_version", re.IGNORECASE
)


def test_only_review_writes_verified_head() -> None:
    """docs/architecture/11 §1: gnosis.review is the only writer of entity_head.verified_version."""
    offenders = [
        str(path.relative_to(ROOT))
        for base in (ROOT / "gnosis", ROOT / "services")
        for path in base.rglob("*.py")
        if VERIFIED_WRITE.search(path.read_text(encoding="utf-8")) and "gnosis/review/" not in path.as_posix()
    ]
    assert offenders == []


def test_rule_detects_the_review_write() -> None:
    """Guard against a vacuous pass: the pattern must match the one legitimate writer."""
    assert VERIFIED_WRITE.search((ROOT / "gnosis" / "review" / "service.py").read_text(encoding="utf-8"))
