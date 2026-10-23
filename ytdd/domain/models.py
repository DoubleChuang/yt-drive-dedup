"""Domain model: the concepts the tool reasons about, with no I/O attached.

Nothing in this module imports Google libraries, reads files, or prints. That is
what makes the matching rules testable without a network or a token.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class DriveVideo:
    """A video file sitting in the user's Google Drive."""

    id: str
    name: str
    size: int | None
    duration_ms: int | None
    modified: str
    owner: str
    owned_by_me: bool

    @property
    def can_delete(self) -> bool:
        """Google only lets the owner trash a file; shared files are read-only here."""
        return self.owned_by_me


@dataclass(frozen=True, slots=True)
class YouTubeVideo:
    """A video already uploaded to the authenticated account's channel."""

    id: str
    title: str
    duration_ms: int | None
    original_filename: str | None
    file_size: int | None
    published_at: str

    @property
    def url(self) -> str:
        return f"https://youtu.be/{self.id}"


class Tier(Enum):
    """How much the evidence says a pair is the same file."""

    STRONG = "STRONG"
    REVIEW = "REVIEW"

    @property
    def is_deletable(self) -> bool:
        return self is Tier.STRONG


@dataclass(frozen=True, slots=True)
class Match:
    """A Drive file paired with the YouTube upload it most likely became."""

    drive: DriveVideo
    youtube: YouTubeVideo
    score: int
    tier: Tier
    evidence: tuple[str, ...]

    @property
    def evidenced_by_bytes(self) -> bool:
        """True when an exact size or original filename proves the identity."""
        return "size=" in self.evidence or "filename=" in self.evidence

    @property
    def evidence_text(self) -> str:
        return ",".join(self.evidence)


@dataclass(frozen=True, slots=True)
class AmbiguousMatch:
    """A pair rejected because several Drive files claimed the same upload."""

    match: Match
    reason: str
