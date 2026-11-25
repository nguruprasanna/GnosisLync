"""Role profiles (docs/architecture/04-roles-skills-registry.md §2).

Profiles are versioned YAML files under ``roles/``. Phase 1 uses them for review authority; prompt framing,
knowledge-access scopes and allowed skills are loaded and validated now so later phases can rely on them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gnosis.knowledge.lifecycle import ReviewAuthority, Significance

SEMVER = r"^[0-9]+\.[0-9]+\.[0-9]+$"


class RoleProfileError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnowledgeAccess(_Strict):
    scopes: frozenset[str] = frozenset({"GENERIC", "PRODUCT", "SITE"})
    entity_kinds_deny: frozenset[str] = frozenset()


class CanApprove(_Strict):
    domains: frozenset[str]
    max_financial_significance: Significance
    entity_kinds: frozenset[str] = frozenset()

    @field_validator("max_financial_significance", mode="before")
    @classmethod
    def _sig(cls, v: Any) -> Significance:
        if isinstance(v, str):
            try:
                return Significance[v]
            except KeyError as exc:
                raise ValueError(f"unknown significance {v!r}") from exc
        return Significance(v)


class ReviewAuthoritySpec(_Strict):
    can_approve: CanApprove | None = None


class RoleProfile(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=SEMVER)
    display_name: str
    domains: frozenset[str] = frozenset()
    products: frozenset[str] = frozenset()
    knowledge_access: KnowledgeAccess = KnowledgeAccess()
    allowed_skills: frozenset[str] = frozenset()
    cautions: tuple[str, ...] = ()
    review_authority: ReviewAuthoritySpec = ReviewAuthoritySpec()

    def authority(self) -> ReviewAuthority | None:
        spec = self.review_authority.can_approve
        if spec is None:
            return None
        return ReviewAuthority(
            domains=spec.domains,
            max_significance=spec.max_financial_significance,
            entity_kinds=spec.entity_kinds,
        )


class RoleRegistry:
    def __init__(self, profiles: Mapping[str, RoleProfile]) -> None:
        self._profiles = dict(profiles)

    @classmethod
    def load(cls, directory: Path) -> RoleRegistry:
        profiles: dict[str, RoleProfile] = {}
        for path in sorted(directory.glob("*.yaml")):
            try:
                profile = RoleProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            except (yaml.YAMLError, ValueError) as exc:
                raise RoleProfileError(f"{path.name}: {exc}") from exc
            if profile.id != path.stem:
                raise RoleProfileError(f"{path.name}: id {profile.id!r} must match the file name")
            profiles[profile.id] = profile
        return cls(profiles)

    def get(self, role_id: str) -> RoleProfile | None:
        return self._profiles.get(role_id)

    def ids(self) -> frozenset[str]:
        return frozenset(self._profiles)
