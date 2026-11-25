from pathlib import Path

import pytest

from gnosis.knowledge.lifecycle import Significance
from gnosis.roles import RoleProfileError, RoleRegistry

pytestmark = [pytest.mark.unit]

ROLES = Path(__file__).resolve().parents[2] / "roles"


def test_repository_profiles_load() -> None:
    reg = RoleRegistry.load(ROLES)
    assert "lending_aa_specialist" in reg.ids()
    lending = reg.get("lending_aa_specialist")
    assert lending is not None
    auth = lending.authority()
    assert auth is not None
    assert auth.max_significance is Significance.HIGH
    assert auth.covers(frozenset({"LENDING"}), Significance.HIGH, "business_rule")
    assert not auth.covers(frozenset({"LENDING"}), Significance.HIGH, "schema_object")
    assert not auth.covers(frozenset({"PAYMENTS"}), Significance.LOW, "business_rule")


def test_unknown_role_is_none() -> None:
    assert RoleRegistry.load(ROLES).get("root") is None


def _write(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / f"{name}.yaml").write_text(body, encoding="utf-8")
    return tmp_path


BASE = "id: {id}\nversion: 1.0.0\ndisplay_name: X\n"


def test_profile_without_review_authority(tmp_path: Path) -> None:
    reg = RoleRegistry.load(_write(tmp_path, "viewer", BASE.format(id="viewer")))
    profile = reg.get("viewer")
    assert profile is not None
    assert profile.authority() is None


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("mismatch", BASE.format(id="other")),
        ("extra", BASE.format(id="extra") + "superuser: true\n"),
        ("badsem", "id: badsem\nversion: one\ndisplay_name: X\n"),
        (
            "badsig",
            BASE.format(id="badsig")
            + "review_authority:\n  can_approve:\n    domains: [GL]\n    max_financial_significance: HUGE\n",
        ),
        ("broken", "id: [unclosed\n"),
    ],
)
def test_invalid_profiles_rejected(tmp_path: Path, name: str, body: str) -> None:
    with pytest.raises(RoleProfileError):
        RoleRegistry.load(_write(tmp_path, name, body))


def test_numeric_significance_accepted(tmp_path: Path) -> None:
    body = BASE.format(id="num") + (
        "review_authority:\n  can_approve:\n    domains: [GL]\n    max_financial_significance: 2\n"
    )
    profile = RoleRegistry.load(_write(tmp_path, "num", body)).get("num")
    assert profile is not None
    auth = profile.authority()
    assert auth is not None
    assert auth.max_significance is Significance.MEDIUM
