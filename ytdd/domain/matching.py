"""Matching rules: pure functions over the domain model.

The scoring weights live in one `MatchPolicy` object instead of scattered
constants, so tuning the heuristics means changing data, not code paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import AmbiguousMatch, DriveVideo, Match, Tier, YouTubeVideo

VIDEO_EXTENSION = re.compile(
    r"\.(mp4|mov|avi|mkv|m4v|wmv|flv|webm|ts|mts|3gp|mpg|mpeg)$", re.IGNORECASE
)

# "clip copy 2" / "clip (1)" / "clip_1" — only meaningful once the extension is gone.
_COPY_SUFFIX = re.compile(
    r"(?:[\s_\-]*[\(\[]\s*(?:copy|副本|\d{1,2})\s*[\)\]]"
    r"|[\s_\-]+(?:copy|副本)(?:[\s_\-]*\d{1,2})?"
    r"|[\s_\-]+\d{1,2})\s*$",
    re.IGNORECASE,
)

# Characters and filler words that carry no identity: "IMG_0231 copy.MP4" == "img0231".
_NOISE = re.compile(
    r"[\s_\-\.·・,，、()\[\]{}【】<>「」『』]|copy|副本|最終|final|輸出|output",
    re.IGNORECASE,
)


def normalize(text: str | None) -> str:
    """Reduce a filename or title to a comparable key.

    >>> normalize("2026 大阪之旅 Day2 (1).mp4") == normalize("2026 大阪之旅 Day2.MOV")
    True
    """
    if not text:
        return ""
    stripped = VIDEO_EXTENSION.sub("", str(text))
    stripped = _COPY_SUFFIX.sub("", stripped)
    return _NOISE.sub("", stripped).lower()


@dataclass(frozen=True, slots=True)
class MatchPolicy:
    """Point values and thresholds for one comparison."""

    size_equal: int = 55
    filename_equal: int = 40
    filename_normalized: int = 30
    title_equal: int = 25
    title_contains: int = 15
    duration_equal: int = 40
    duration_near: int = 18

    # Containment needs a real word: the channel has uploads titled "1" and "2",
    # and a single digit is a substring of almost every filename.
    min_title_chars: int = 5
    duration_equal_window_ms: int = 2_000
    duration_near_window_ms: int = 10_000

    strong_threshold: int = 85
    review_threshold: int = 55
    max_score: int = 100


DEFAULT_POLICY = MatchPolicy()


@dataclass(frozen=True, slots=True)
class Score:
    points: int = 0
    evidence: tuple[str, ...] = ()

    def with_points(self, points: int, tag: str) -> "Score":
        return Score(self.points + points, self.evidence + (tag,))


def score_pair(
    drive: DriveVideo, youtube: YouTubeVideo, policy: MatchPolicy = DEFAULT_POLICY
) -> Score:
    """Score one candidate pair. Independent signals, additive."""
    score = Score()
    score = _score_identity(drive, youtube, policy, score)
    score = _score_duration(drive, youtube, policy, score)
    return Score(min(score.points, policy.max_score), score.evidence)


def _score_identity(
    drive: DriveVideo, youtube: YouTubeVideo, policy: MatchPolicy, score: Score
) -> Score:
    if drive.size and youtube.file_size and drive.size == youtube.file_size:
        score = score.with_points(policy.size_equal, "size=")

    original = youtube.original_filename
    if original and original == drive.name:
        return score.with_points(policy.filename_equal, "filename=")
    if original and normalize(original) == normalize(drive.name):
        return score.with_points(policy.filename_normalized, "filename~")

    title_key, name_key = normalize(youtube.title), normalize(drive.name)
    if title_key and title_key == name_key:
        return score.with_points(policy.title_equal, "title=")
    long_enough = len(title_key) >= policy.min_title_chars
    if long_enough and (title_key in name_key or name_key in title_key):
        return score.with_points(policy.title_contains, "title~")
    return score


def _score_duration(
    drive: DriveVideo, youtube: YouTubeVideo, policy: MatchPolicy, score: Score
) -> Score:
    if not drive.duration_ms or not youtube.duration_ms:
        return score
    delta = abs(drive.duration_ms - youtube.duration_ms)
    if delta <= policy.duration_equal_window_ms:
        return score.with_points(policy.duration_equal, "dur=")
    if delta <= policy.duration_near_window_ms:
        return score.with_points(policy.duration_near, "dur~")
    return score


def classify(score: int, policy: MatchPolicy = DEFAULT_POLICY) -> Tier | None:
    """Map points to a tier, or None when the evidence is not worth reporting."""
    if score >= policy.strong_threshold:
        return Tier.STRONG
    if score >= policy.review_threshold:
        return Tier.REVIEW
    return None


def best_match(
    drive: DriveVideo,
    candidates: Iterable[YouTubeVideo],
    policy: MatchPolicy = DEFAULT_POLICY,
) -> Match | None:
    """Strongest scoring candidate for one Drive file, if any clears the bar."""
    best: Match | None = None
    for youtube in candidates:
        score = score_pair(drive, youtube, policy)
        tier = classify(score.points, policy)
        if tier is None:
            continue
        if best is None or score.points > best.score:
            best = Match(drive, youtube, score.points, tier, score.evidence)
    return best


@dataclass(frozen=True, slots=True)
class Resolution:
    """Result of matching every Drive file, before and after the uniqueness rule."""

    accepted: tuple[Match, ...] = ()
    ambiguous: tuple[AmbiguousMatch, ...] = ()
    unmatched: tuple[DriveVideo, ...] = ()

    @classmethod
    def build(
        cls,
        drive_videos: Sequence[DriveVideo],
        youtube_videos: Sequence[YouTubeVideo],
        policy: MatchPolicy = DEFAULT_POLICY,
    ) -> "Resolution":
        raw = [
            match
            for video in drive_videos
            if (match := best_match(video, youtube_videos, policy)) is not None
        ]
        accepted, ambiguous = enforce_unique_upload(raw)
        matched_ids = {m.drive.id for m in accepted}
        return cls(
            accepted=tuple(_sorted(accepted)),
            ambiguous=tuple(ambiguous),
            unmatched=tuple(v for v in drive_videos if v.id not in matched_ids),
        )


def enforce_unique_upload(
    matches: Sequence[Match],
) -> tuple[list[Match], list[AmbiguousMatch]]:
    """Keep at most one Drive file per YouTube upload.

    One upload came from one file. When several Drive files claim the same
    video, only an exact size or original filename can break the tie; otherwise
    all of them are coincidence and none may be deleted.
    """
    by_upload: dict[str, list[Match]] = {}
    for match in matches:
        by_upload.setdefault(match.youtube.id, []).append(match)

    accepted: list[Match] = []
    rejected: list[AmbiguousMatch] = []
    for group in by_upload.values():
        if len(group) == 1:
            accepted.append(group[0])
            continue
        proven = [m for m in group if m.evidenced_by_bytes]
        if not proven:
            rejected.extend(
                AmbiguousMatch(m, "多個 Drive 檔案對到同一支 YouTube 影片，沒有大小或原檔名鐵證")
                for m in group
            )
            continue
        winner = max(proven, key=lambda m: m.score)
        accepted.append(winner)
        rejected.extend(
            AmbiguousMatch(m, "同一支 YouTube 影片已被另一個 Drive 檔案以鐵證認領")
            for m in group
            if m is not winner
        )
    return accepted, rejected


def _sorted(matches: Sequence[Match]) -> list[Match]:
    """Stable presentation order: most confident first, then by filename."""
    return sorted(matches, key=lambda m: (-m.score, m.drive.name.lower()))
