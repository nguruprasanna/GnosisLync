import pytest
from pydantic import ValidationError

from gnosis.knowledge.lifecycle import Significance
from gnosis.knowledge.registry import VersionContent, parse_significance

pytestmark = [pytest.mark.unit, pytest.mark.knowledge]


def test_parse_significance_variants() -> None:
    assert parse_significance(Significance.HIGH) is Significance.HIGH
    assert parse_significance("MEDIUM") is Significance.MEDIUM
    assert parse_significance(1) is Significance.LOW
    with pytest.raises(ValueError, match="unknown financial significance"):
        parse_significance("HUGE")


def test_release_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="release_from"):
        VersionContent(name="x", release_from=23, release_to=22)


def test_content_hash_depends_on_key_and_content_not_status() -> None:
    a = VersionContent(name="x", definition="d")
    assert a.content_hash("k1") != a.content_hash("k2")
    assert a.content_hash("k1") != VersionContent(name="x", definition="e").content_hash("k1")
    assert a.content_hash("k1") == VersionContent(status="DRAFT", name="x", definition="d").content_hash("k1")
    assert a.canonical()["financial_significance"] == "NONE"
