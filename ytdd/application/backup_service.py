"""Use case: copy Drive videos to YouTube, then reclaim the Drive space.

Two invariants drive the design:

* Never upload something the channel already has — that spends a very scarce
  quota (1600 units per upload, 10 000 per day by default) on a duplicate.
* Never delete the Drive source until YouTube reports the upload as processed.
  An unprocessed upload is not a backup yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..domain.matching import DEFAULT_POLICY, MatchPolicy, Resolution
from ..domain.models import DriveVideo, Match, Tier
from ..errors import QuotaExceeded
from .ports import (
    DriveFileFetcher,
    DriveTrashWriter,
    DriveVideoReader,
    UploadProgress,
    YouTubeUploadReader,
    YouTubeUploader,
)

DEFAULT_PROCESS_TIMEOUT_S = 900.0
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000

# videos.insert costs 1600 quota units and a new Cloud project gets 10 000 per
# day, so a fresh project can upload six videos a day until the quota is raised.
UPLOAD_COST_UNITS = 1600
DEFAULT_DAILY_QUOTA_UNITS = 10_000


def uploads_per_day(quota_units: int = DEFAULT_DAILY_QUOTA_UNITS) -> int:
    """How many uploads today's quota covers. Zero-config projects get 6."""
    return max(quota_units // UPLOAD_COST_UNITS, 0)


@dataclass(frozen=True, slots=True)
class BackupPlan:
    """What a backup run would do, before it spends any quota."""

    uploadable: tuple[DriveVideo, ...] = ()
    already_on_youtube: tuple[Match, ...] = ()
    needs_review: tuple[Match, ...] = ()
    not_owned: int = 0

    @property
    def upload_bytes(self) -> int:
        return sum(video.size or 0 for video in self.uploadable)


@dataclass(frozen=True, slots=True)
class UploadResult:
    video: DriveVideo
    youtube_id: str | None = None
    source_removed: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.youtube_id is not None


@dataclass(frozen=True, slots=True)
class BackupReport:
    results: tuple[UploadResult, ...] = ()
    stopped_by_quota: bool = False
    not_attempted: int = 0

    @property
    def uploaded(self) -> tuple[UploadResult, ...]:
        return tuple(r for r in self.results if r.youtube_id)

    @property
    def removed_from_drive(self) -> tuple[UploadResult, ...]:
        return tuple(r for r in self.results if r.source_removed)

    @property
    def failed(self) -> tuple[UploadResult, ...]:
        return tuple(r for r in self.results if r.error)


class BackupService:
    """Plan a backup, then execute it one file at a time."""

    def __init__(
        self,
        drive_reader: DriveVideoReader,
        fetcher: DriveFileFetcher,
        youtube_reader: YouTubeUploadReader,
        uploader: YouTubeUploader,
        trash_writer: DriveTrashWriter,
        policy: MatchPolicy = DEFAULT_POLICY,
    ) -> None:
        self._drive = drive_reader
        self._fetcher = fetcher
        self._youtube = youtube_reader
        self._uploader = uploader
        self._trash = trash_writer
        self._policy = policy

    def plan(self, *, include_review: bool = False) -> BackupPlan:
        """Select owned videos that the channel does not already have.

        Only owned files: you cannot reclaim space for a file someone else
        owns, and pushing their content to your channel is not ours to decide.
        """
        all_videos = self._drive.list_videos()
        owned = [v for v in all_videos if v.owned_by_me]

        resolution = Resolution.build(all_videos, self._youtube.list_uploads(), self._policy)
        already = tuple(m for m in resolution.accepted if m.tier is Tier.STRONG)
        review = tuple(m for m in resolution.accepted if m.tier is Tier.REVIEW)

        # A REVIEW match is probably the same video. Uploading it again wastes a
        # scarce upload quota and creates a duplicate; only do it on request.
        skip = {m.drive.id for m in already}
        if not include_review:
            skip |= {m.drive.id for m in review}

        return BackupPlan(
            uploadable=tuple(v for v in owned if v.id not in skip),
            already_on_youtube=already,
            needs_review=review,
            not_owned=len(all_videos) - len(owned),
        )

    def execute(
        self,
        plan: BackupPlan,
        *,
        work_dir: Path,
        privacy: str = "private",
        remove_source: bool = True,
        limit: int | None = None,
        process_timeout_s: float = DEFAULT_PROCESS_TIMEOUT_S,
        on_progress: UploadProgress | None = None,
        on_message=None,
    ) -> BackupReport:
        notify = on_message or (lambda _: None)
        queue: Sequence[DriveVideo] = plan.uploadable[:limit] if limit else plan.uploadable
        results: list[UploadResult] = []
        stopped_by_quota = False

        for index, video in enumerate(queue, start=1):
            notify(f"[{index}/{len(queue)}] {video.name}")
            try:
                result = self._upload_one(
                    video,
                    work_dir=work_dir,
                    privacy=privacy,
                    remove_source=remove_source,
                    process_timeout_s=process_timeout_s,
                    on_progress=on_progress,
                    notify=notify,
                )
            except QuotaExceeded as exc:
                stopped_by_quota = True
                notify(f"  配額用盡：{exc}")
                break
            results.append(result)

        attempted = len(results)
        return BackupReport(
            results=tuple(results),
            stopped_by_quota=stopped_by_quota,
            not_attempted=len(queue) - attempted,
        )

    # -- internals ------------------------------------------------------

    def _upload_one(
        self,
        video: DriveVideo,
        *,
        work_dir: Path,
        privacy: str,
        remove_source: bool,
        process_timeout_s: float,
        on_progress: UploadProgress | None,
        notify,
    ) -> UploadResult:
        local_path = work_dir / f"{video.id}{_suffix(video.name)}"
        try:
            notify("  downloading from Drive ...")
            self._fetcher.download_to(video.id, local_path)

            notify("  uploading to YouTube ...")
            youtube_id = self._uploader.upload(
                file_path=local_path,
                title=_title(video),
                description=_description(video),
                privacy=privacy,
                on_progress=on_progress,
            )

            if not remove_source:
                notify("  uploaded (Drive 原檔保留)")
                return UploadResult(video, youtube_id=youtube_id)

            notify("  waiting for YouTube to finish processing ...")
            if not self._uploader.wait_until_processed(youtube_id, process_timeout_s):
                notify("  尚未處理完成，保留 Drive 原檔")
                return UploadResult(
                    video,
                    youtube_id=youtube_id,
                    error="YouTube 尚未處理完成，Drive 原檔保留",
                )

            self._trash.trash(video.id)
            notify("  已上傳並移除 Drive 原檔（垃圾桶）")
            return UploadResult(video, youtube_id=youtube_id, source_removed=True)
        except QuotaExceeded:
            raise
        except Exception as exc:  # one bad file must not abort the batch
            notify(f"  失敗：{exc}")
            return UploadResult(video, error=str(exc))
        finally:
            local_path.unlink(missing_ok=True)


def _title(video: DriveVideo) -> str:
    stem = Path(video.name).stem or video.name
    return stem[:TITLE_LIMIT]


def _description(video: DriveVideo) -> str:
    lines = [
        "由 Google Drive 備份上傳（yt-drive-dedup）",
        f"原檔名：{video.name}",
    ]
    if video.modified:
        lines.append(f"原檔日期：{video.modified}")
    if video.duration_ms:
        lines.append(f"長度：{video.duration_ms // 1000} 秒")
    return "\n".join(lines)[:DESCRIPTION_LIMIT]


def _suffix(name: str) -> str:
    suffix = Path(name).suffix
    return suffix if len(suffix) <= 10 else ".bin"
