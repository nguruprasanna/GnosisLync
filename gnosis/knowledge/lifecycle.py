"""Knowledge version lifecycle (docs/architecture/02-knowledge-data-model.md §4).

This module is the authoritative, unit-tested statement of the lifecycle rules. The database trigger
``gnosis_check_lifecycle`` (db/sql/0001_core.sql) enforces the same invariants as a backstop; the
reviewer-authority and four-eyes rules below need role information and are enforced here only.

Hard invariants:
  1. CANDIDATE can never move directly to VERIFIED.
  2. VERIFIED needs a non-AI verification basis and either an APPROVE review decision or deterministic
     auto-verification (OBSERVED metadata fact, basis SOURCE_METADATA/CODE_OBSERVED, significance NONE/LOW).
  3. MEDIUM/HIGH financial significance always needs a review decision by a reviewer whose authority covers
     the item's domain and significance; HIGH needs two distinct approvers.
  4. Version content is immutable (enforced in the database; new content means a new version).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from uuid import UUID


class Status(StrEnum):
    DRAFT = "DRAFT"
    CANDIDATE = "CANDIDATE"
    OBSERVED = "OBSERVED"
    UNDER_REVIEW = "UNDER_REVIEW"
    PROVISIONAL = "PROVISIONAL"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    DEPRECATED = "DEPRECATED"
    INCORRECT = "INCORRECT"


class Basis(StrEnum):
    SOURCE_METADATA = "SOURCE_METADATA"
    CODE_OBSERVED = "CODE_OBSERVED"
    DOCUMENTATION = "DOCUMENTATION"
    SPECIALIST_APPROVED = "SPECIALIST_APPROVED"
    DATA_PROFILED = "DATA_PROFILED"
    AI_INFERRED = "AI_INFERRED"


class Significance(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Decision(StrEnum):
    APPROVE = "APPROVE"
    APPROVE_PROVISIONAL = "APPROVE_PROVISIONAL"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


INITIAL_STATUSES = frozenset({Status.DRAFT, Status.CANDIDATE, Status.OBSERVED})
DETERMINISTIC_BASES = frozenset({Basis.SOURCE_METADATA, Basis.CODE_OBSERVED})

# Must stay identical to the rows of table lifecycle_transition (checked by tests/db/test_lifecycle_db.py).
TRANSITIONS: frozenset[tuple[Status, Status]] = frozenset(
    {
        (Status.DRAFT, Status.UNDER_REVIEW),
        (Status.CANDIDATE, Status.UNDER_REVIEW),
        (Status.OBSERVED, Status.VERIFIED),
        (Status.OBSERVED, Status.UNDER_REVIEW),
        (Status.UNDER_REVIEW, Status.VERIFIED),
        (Status.UNDER_REVIEW, Status.PROVISIONAL),
        (Status.UNDER_REVIEW, Status.REJECTED),
        (Status.UNDER_REVIEW, Status.CANDIDATE),
        (Status.PROVISIONAL, Status.VERIFIED),
        (Status.PROVISIONAL, Status.INCORRECT),
        (Status.PROVISIONAL, Status.SUPERSEDED),
        (Status.PROVISIONAL, Status.DEPRECATED),
        (Status.VERIFIED, Status.SUPERSEDED),
        (Status.VERIFIED, Status.DEPRECATED),
        (Status.VERIFIED, Status.INCORRECT),
    }
)


class LifecycleError(ValueError):
    """Raised when a requested creation or transition violates a lifecycle invariant."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class ReviewAuthority:
    """Review authority granted by a role profile (docs/architecture/04 §2)."""

    domains: frozenset[str]
    max_significance: Significance
    entity_kinds: frozenset[str] = frozenset()  # empty = any kind

    def covers(
        self, item_domains: frozenset[str], significance: Significance, kind: str | None = None
    ) -> bool:
        if significance > self.max_significance:
            return False
        if kind is not None and self.entity_kinds and kind not in self.entity_kinds:
            return False
        return not item_domains or bool(item_domains & self.domains)


@dataclass(frozen=True)
class Approval:
    reviewer_id: UUID
    decision: Decision
    authority: ReviewAuthority


@dataclass(frozen=True)
class VersionState:
    status: Status
    basis: Basis | None
    significance: Significance
    domains: frozenset[str] = field(default_factory=frozenset)
    kind: str | None = None


def check_creation(status: Status, basis: Basis | None) -> None:
    """Validate the status of a newly created version."""
    if status not in INITIAL_STATUSES:
        raise LifecycleError(
            "ILLEGAL_INITIAL_STATUS", f"new versions must start as DRAFT/CANDIDATE/OBSERVED, not {status}"
        )
    if status is Status.OBSERVED and basis not in DETERMINISTIC_BASES:
        raise LifecycleError(
            "OBSERVED_REQUIRES_DETERMINISTIC_BASIS", "OBSERVED requires SOURCE_METADATA or CODE_OBSERVED"
        )


def required_approvals(significance: Significance) -> int:
    """Number of distinct approvers needed to verify an item (four-eyes for HIGH)."""
    return 2 if significance is Significance.HIGH else 1


def _qualified(approvals: list[Approval], state: VersionState, accepted: set[Decision]) -> set[UUID]:
    return {
        a.reviewer_id
        for a in approvals
        if a.decision in accepted and a.authority.covers(state.domains, state.significance, state.kind)
    }


def check_transition(
    current: VersionState,
    target: Status,
    new_basis: Basis | None = None,
    approvals: list[Approval] | None = None,
) -> Basis | None:
    """Validate a lifecycle transition. Returns the verification basis the new state will carry.

    ``approvals`` are the review decisions recorded for this item in its change set.
    """
    approvals = approvals or []
    if (current.status, target) not in TRANSITIONS:
        raise LifecycleError("ILLEGAL_TRANSITION", f"{current.status} -> {target}")
    basis = new_basis or current.basis

    if any(a.decision is Decision.REJECT for a in approvals) and target is not Status.REJECTED:
        raise LifecycleError("REJECTED_BY_REVIEWER", "an approval cannot override a recorded rejection")

    if target is Status.VERIFIED:
        if basis is None or basis is Basis.AI_INFERRED:
            raise LifecycleError("NON_AI_BASIS_REQUIRED", "VERIFIED requires a non-AI verification basis")
        auto = (
            current.status is Status.OBSERVED
            and basis in DETERMINISTIC_BASES
            and current.significance <= Significance.LOW
            and not approvals
        )
        if not auto:
            approvers = _qualified(approvals, current, {Decision.APPROVE})
            needed = required_approvals(current.significance)
            if len(approvers) < needed:
                raise LifecycleError(
                    "INSUFFICIENT_APPROVALS",
                    f"{needed} qualified distinct approver(s) required, {len(approvers)} present",
                )
    elif target is Status.PROVISIONAL:
        if not _qualified(approvals, current, {Decision.APPROVE, Decision.APPROVE_PROVISIONAL}):
            raise LifecycleError("INSUFFICIENT_APPROVALS", "PROVISIONAL requires a qualified approval")
    elif target is Status.REJECTED:
        if not any(a.decision is Decision.REJECT for a in approvals):
            raise LifecycleError("REJECT_DECISION_REQUIRED", "REJECTED requires a REJECT decision")
    elif target in (Status.INCORRECT, Status.DEPRECATED):
        approvers = _qualified(approvals, current, {Decision.APPROVE})
        if len(approvers) < required_approvals(current.significance):
            raise LifecycleError("INSUFFICIENT_APPROVALS", f"{target} requires qualified approval")
    return basis
