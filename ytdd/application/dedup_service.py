"""Application layer: one use case, "remove Drive copies that YouTube already has".

Orchestrates ports; knows nothing about argparse, CSV, or the Drive API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

ProgressCallback = Callable[[str], None]

from ..domain.matching import DEFAULT_POLICY, MatchPolicy, Resolution
from ..domain.models import DriveVideo, Match, Tier, YouTubeVideo
from .ports import DriveTrashWriter, DriveVideoReader, YouTubeUploadReader


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Everything one scan produced, ready for a presenter to render."""

    drive_videos: tuple[DriveVideo, ...]
    youtube_videos: tuple[YouTubeVideo, ...]
    resolution: Resolution

    @property
    def drive_total(self) -> int:
        return len(self.drive_videos)

    @property
    def youtube_total(self) -> int:
        return len(self.youtube_videos)

    def of_tier(self, tier: Tier) -> tuple[Match, ...]:
        return tuple(m for m in self.resolution.accepted if m.tier is tier)

    @property
    def strong(self) -> tuple[Match, ...]:
        return self.of_tier(Tier.STRONG)

    @property
    def review(self) -> tuple[Match, ...]:
        return self.of_tier(Tier.REVIEW)

    @property
    def unmatched_uploads(self) -> tuple[YouTubeVideo, ...]:
        """Uploads that no accepted match points at — candidates for 'already deleted'."""
        claimed = {m.youtube.id for m in self.resolution.accepted}
        return tuple(v for v in self.youtube_videos if v.id not in claimed)


@dataclass(frozen=True, slots=True)
class DeletionFailure:
    match: Match
    reason: str


@dataclass(frozen=True, slots=True)
class DeletionOutcome:
    deleted: tuple[Match, ...] = ()
    skipped_not_owned: tuple[Match, ...] = ()
    failures: tuple[DeletionFailure, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures


class DedupService:
    """The single use case: find duplicates, then delete the ones we are allowed to."""

    def __init__(
        self,
        drive_reader: DriveVideoReader,
        youtube_reader: YouTubeUploadReader,
        trash_writer: DriveTrashWriter,
        policy: MatchPolicy = DEFAULT_POLICY,
    ) -> None:
        self._drive = drive_reader
        self._youtube = youtube_reader
        self._trash = trash_writer
        self._policy = policy

    def scan(
        self,
        *,
        owned_only: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> ScanResult:
        notify = on_progress or (lambda _: None)

        notify("reading Drive videos ...")
        videos: Sequence[DriveVideo] = self._drive.list_videos()
        if owned_only:
            videos = [v for v in videos if v.owned_by_me]
        notify(f"  {len(videos)} video files in Drive")

        notify("reading YouTube uploads ...")
        uploads = self._youtube.list_uploads()
        notify(f"  {len(uploads)} videos on the channel")

        return ScanResult(
            drive_videos=tuple(videos),
            youtube_videos=tuple(uploads),
            resolution=Resolution.build(videos, uploads, self._policy),
        )

    def delete(self, matches: Sequence[Match], *, permanent: bool = False) -> DeletionOutcome:
        deleted: list[Match] = []
        skipped: list[Match] = []
        failures: list[DeletionFailure] = []

        for match in matches:
            if not match.drive.can_delete:
                skipped.append(match)
                continue
            try:
                if permanent:
                    self._trash.delete_permanently(match.drive.id)
                else:
                    self._trash.trash(match.drive.id)
            except Exception as exc:  # one bad file must not abort the batch
                failures.append(DeletionFailure(match, str(exc)))
                continue
            deleted.append(match)

        return DeletionOutcome(tuple(deleted), tuple(skipped), tuple(failures))
