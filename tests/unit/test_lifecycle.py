import itertools
import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gnosis.knowledge.lifecycle import (
    TRANSITIONS,
    Approval,
    Basis,
    Decision,
    LifecycleError,
    ReviewAuthority,
    Significance,
    Status,
    VersionState,
    check_creation,
    check_transition,
    required_approvals,
)

pytestmark = [pytest.mark.unit, pytest.mark.knowledge]

LENDING_HIGH = ReviewAuthority(frozenset({"LENDING"}), Significance.HIGH)
LENDING_LOW = ReviewAuthority(frozenset({"LENDING"}), Significance.LOW)
GL_HIGH = ReviewAuthority(frozenset({"GL"}), Significance.HIGH)


def approve(authority: ReviewAuthority = LENDING_HIGH, decision: Decision = Decision.APPROVE) -> Approval:
    return Approval(uuid.uuid4(), decision, authority)


def state(
    status: Status, basis: Basis | None = Basis.DOCUMENTATION, sig: Significance = Significance.NONE
) -> VersionState:
    return VersionState(status, basis, sig, frozenset({"LENDING"}))


# ---------------------------------------------------------------- creation
@pytest.mark.parametrize("status", [Status.DRAFT, Status.CANDIDATE])
def test_creation_allowed_states(status: Status) -> None:
    check_creation(status, Basis.AI_INFERRED)


@pytest.mark.parametrize("status", sorted(set(Status) - {Status.DRAFT, Status.CANDIDATE, Status.OBSERVED}))
def test_creation_rejects_non_initial_states(status: Status) -> None:
    with pytest.raises(LifecycleError) as e:
        check_creation(status, Basis.SOURCE_METADATA)
    assert e.value.code == "ILLEGAL_INITIAL_STATUS"


@pytest.mark.parametrize("basis", [None, Basis.AI_INFERRED, Basis.DOCUMENTATION, Basis.DATA_PROFILED])
def test_observed_requires_deterministic_basis(basis: Basis | None) -> None:
    with pytest.raises(LifecycleError):
        check_creation(Status.OBSERVED, basis)


# ---------------------------------------------------------------- invariant 1
@given(st.lists(st.sampled_from(list(Decision)), max_size=3), st.sampled_from(list(Basis)))
def test_candidate_never_directly_verified(decisions: list[Decision], basis: Basis) -> None:
    approvals = [approve(decision=d) for d in decisions]
    with pytest.raises(LifecycleError) as e:
        check_transition(state(Status.CANDIDATE), Status.VERIFIED, basis, approvals)
    assert e.value.code == "ILLEGAL_TRANSITION"


def test_all_unlisted_transitions_are_illegal() -> None:
    for frm, to in itertools.product(Status, Status):
        if (frm, to) in TRANSITIONS:
            continue
        with pytest.raises(LifecycleError):
            check_transition(state(frm), to, Basis.SPECIALIST_APPROVED, [approve(), approve()])


# ---------------------------------------------------------------- invariant 2
def test_ai_inferred_cannot_be_verified() -> None:
    with pytest.raises(LifecycleError) as e:
        check_transition(state(Status.UNDER_REVIEW, Basis.AI_INFERRED), Status.VERIFIED, None, [approve()])
    assert e.value.code == "NON_AI_BASIS_REQUIRED"


def test_review_can_rebase_ai_inferred_to_specialist_approved() -> None:
    basis = check_transition(
        state(Status.UNDER_REVIEW, Basis.AI_INFERRED), Status.VERIFIED, Basis.SPECIALIST_APPROVED, [approve()]
    )
    assert basis is Basis.SPECIALIST_APPROVED


@pytest.mark.parametrize("sig", [Significance.NONE, Significance.LOW])
def test_observed_metadata_auto_verifies(sig: Significance) -> None:
    assert (
        check_transition(state(Status.OBSERVED, Basis.SOURCE_METADATA, sig), Status.VERIFIED)
        is Basis.SOURCE_METADATA
    )


@pytest.mark.parametrize("sig", [Significance.MEDIUM, Significance.HIGH])
def test_financial_observed_fact_needs_review(sig: Significance) -> None:
    with pytest.raises(LifecycleError) as e:
        check_transition(state(Status.OBSERVED, Basis.SOURCE_METADATA, sig), Status.VERIFIED)
    assert e.value.code == "INSUFFICIENT_APPROVALS"


def test_documentation_basis_needs_review() -> None:
    with pytest.raises(LifecycleError):
        check_transition(state(Status.UNDER_REVIEW, Basis.DOCUMENTATION), Status.VERIFIED)


# ---------------------------------------------------------------- invariant 3
def test_high_significance_requires_two_distinct_approvers() -> None:
    s = state(Status.UNDER_REVIEW, Basis.DOCUMENTATION, Significance.HIGH)
    with pytest.raises(LifecycleError):
        check_transition(s, Status.VERIFIED, approvals=[approve()])
    same = approve()
    with pytest.raises(LifecycleError):
        check_transition(s, Status.VERIFIED, approvals=[same, same])
    check_transition(s, Status.VERIFIED, approvals=[approve(), approve()])


def test_reviewer_authority_must_cover_significance() -> None:
    s = state(Status.UNDER_REVIEW, Basis.DOCUMENTATION, Significance.MEDIUM)
    with pytest.raises(LifecycleError):
        check_transition(s, Status.VERIFIED, approvals=[approve(LENDING_LOW)])


def test_reviewer_authority_must_cover_domain() -> None:
    s = state(Status.UNDER_REVIEW, Basis.DOCUMENTATION, Significance.LOW)
    with pytest.raises(LifecycleError):
        check_transition(s, Status.VERIFIED, approvals=[approve(GL_HIGH)])


def test_rejection_cannot_be_overridden() -> None:
    s = state(Status.UNDER_REVIEW)
    with pytest.raises(LifecycleError) as e:
        check_transition(s, Status.VERIFIED, approvals=[approve(), approve(decision=Decision.REJECT)])
    assert e.value.code == "REJECTED_BY_REVIEWER"


def test_reject_requires_reject_decision() -> None:
    with pytest.raises(LifecycleError):
        check_transition(state(Status.UNDER_REVIEW), Status.REJECTED, approvals=[approve()])
    check_transition(
        state(Status.UNDER_REVIEW), Status.REJECTED, approvals=[approve(decision=Decision.REJECT)]
    )


def test_provisional_accepts_provisional_approval() -> None:
    check_transition(
        state(Status.UNDER_REVIEW),
        Status.PROVISIONAL,
        approvals=[approve(decision=Decision.APPROVE_PROVISIONAL)],
    )
    with pytest.raises(LifecycleError):
        check_transition(state(Status.UNDER_REVIEW), Status.PROVISIONAL)


@pytest.mark.parametrize("target", [Status.INCORRECT, Status.DEPRECATED])
def test_marking_verified_incorrect_or_deprecated_needs_approval(target: Status) -> None:
    s = state(Status.VERIFIED, Basis.SPECIALIST_APPROVED, Significance.HIGH)
    with pytest.raises(LifecycleError):
        check_transition(s, target, approvals=[approve()])
    check_transition(s, target, approvals=[approve(), approve()])


def test_superseded_needs_no_approval() -> None:
    check_transition(state(Status.VERIFIED, Basis.SPECIALIST_APPROVED), Status.SUPERSEDED)


def test_request_changes_returns_to_candidate() -> None:
    check_transition(
        state(Status.UNDER_REVIEW), Status.CANDIDATE, approvals=[approve(decision=Decision.REQUEST_CHANGES)]
    )


def test_required_approvals() -> None:
    assert [required_approvals(s) for s in Significance] == [1, 1, 1, 2]


def test_authority_with_empty_item_domains() -> None:
    assert LENDING_LOW.covers(frozenset(), Significance.LOW)
