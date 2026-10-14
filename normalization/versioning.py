"""Central version identifiers for reproducible normalization behavior."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .text import DEFAULT_PROFILE, NormalizationProfile


@dataclass(frozen=True)
class EngineVersions:
    text_profile: NormalizationProfile = DEFAULT_PROFILE
    selector_version: str = "source_text_selector_v2"
    extraction_version: str = "deterministic_extract_v2"
    matching_version: str = "deterministic_match_v1"
    classification_version: str = "classification_v2"

    @property
    def normalization_version(self) -> str:
        return self.text_profile.version


DEFAULT_VERSIONS = EngineVersions()


def new_text_version(version: str) -> EngineVersions:
    """Return the current behavior under a new explicit version identifier.

    Use this when deliberately versioning identical behavior for a controlled
    rollout. Any rule change should update the profile/code and version together.
    """
    return replace(DEFAULT_VERSIONS, text_profile=replace(DEFAULT_PROFILE, version=version))


def new_classification_version(version: str) -> EngineVersions:
    """Return the current engine behavior under a new classifier version."""
    return replace(DEFAULT_VERSIONS, classification_version=version)
