"""Tests for the upload use case, driven through fake ports."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ytdd.application.backup_service import BackupService, uploads_per_day
from ytdd.domain.models import DriveVideo, YouTubeVideo
from ytdd.errors import ApiError, QuotaExceeded


def drive(**overrides) -> DriveVideo:
    defaults = dict(
        id="d1",
        name="clip.mp4",
        size=1_000,
        duration_ms=60_000,
        modified="2026-01-01",
        owner="me@example.com",
        owned_by_me=True,
    )
    return DriveVideo(**{**defaults, **overrides})


def upload(**overrides) -> YouTubeVideo:
    defaults = dict(
        id="y1",
        title="clip",
        duration_ms=60_000,
        original_filename="clip.mp4",
        file_size=1_000,
        published_at="2026-01-02T00:00:00Z",
    )
    return YouTubeVideo(**{**defaults, **overrides})


class FakeDrive:
    def __init__(self, videos, downloads=None):
        self.videos = videos
        self.trashed: list[str] = []
        self.downloaded: list[str] = []
        self._downloads = downloads or {}

    def list_videos(self):
        return self.videos

    def download_to(self, video_id: str, destination: Path) -> Path:
        self.downloaded.append(video_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-video-bytes")
        return destination

    def trash(self, video_id: str) -> None:
        self.trashed.append(video_id)

    def delete_permanently(self, video_id: str) -> None:
        self.trashed.append(video_id)


class FakeUploader:
    def __init__(self, processed: bool = True, quota_after: int | None = None):
        self.uploads: list[tuple[str, str, str]] = []
        self.processed = processed
        self._quota_after = quota_after
        self.counter = 0

    def upload(self, *, file_path, title, description, privacy, on_progress=None):
        if self._quota_after is not None and self.counter >= self._quota_after:
            raise QuotaExceeded("quota gone")
        self.counter += 1
        self.uploads.append((file_path.name, title, privacy))
        if on_progress:
            on_progress(0.5)
            on_progress(1.0)
        return f"yt{self.counter}"

    def wait_until_processed(self, video_id: str, timeout_s: float) -> bool:
        return self.processed


class FakeYouTube:
    def __init__(self, videos):
        self.videos = videos

    def list_uploads(self):
        return self.videos


class FakePlaylists:
    def __init__(self, playlist_id="PL1", fail_add=False, fail_ensure=False):
        self.playlist_id = playlist_id
        self.ensured: list[str] = []
        self.added: list[tuple[str, str]] = []
        self._fail_add = fail_add
        self._fail_ensure = fail_ensure

    def ensure_playlist(self, title: str) -> str:
        if self._fail_ensure:
            raise ApiError("缺少 youtube.force-ssl scope")
        self.ensured.append(title)
        return self.playlist_id

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        if self._fail_add:
            raise ApiError("加入播放清單失敗")
        self.added.append((playlist_id, video_id))


def build(videos, uploads, *, processed=True, quota_after=None, playlists=None):
    fake_drive = FakeDrive(videos)
    uploader = FakeUploader(processed=processed, quota_after=quota_after)
    service = BackupService(
        fake_drive, fake_drive, FakeYouTube(uploads), uploader, fake_drive, playlists=playlists
    )
    return service, fake_drive, uploader


class PlanTests(unittest.TestCase):
    def test_skips_videos_already_on_youtube(self) -> None:
        service, _, _ = build([drive()], [upload()])
        plan = service.plan()
        self.assertEqual(plan.uploadable, ())
        self.assertEqual(len(plan.already_on_youtube), 1)

    def test_only_owned_videos_are_uploadable(self) -> None:
        videos = [drive(id="d1"), drive(id="d2", name="theirs.mp4", owned_by_me=False)]
        service, _, _ = build(videos, [])
        plan = service.plan()
        self.assertEqual([v.id for v in plan.uploadable], ["d1"])
        self.assertEqual(plan.not_owned, 1)

    def test_review_matches_are_skipped_by_default(self) -> None:
        # Title matches and duration agrees, but no size/filename proof -> REVIEW.
        videos = [drive(name="temp_video_1751727719027.mp4", size=999, duration_ms=19_000)]
        uploads = [
            upload(
                id="y1",
                title="temp video 1751727719027",
                duration_ms=19_000,
                file_size=None,
                original_filename=None,
            )
        ]
        service, _, _ = build(videos, uploads)
        self.assertEqual(len(service.plan().needs_review), 1)
        self.assertEqual(service.plan().uploadable, ())
        self.assertEqual(len(service.plan(include_review=True).uploadable), 1)

    def test_upload_bytes_totals_the_plan(self) -> None:
        videos = [drive(id="d1", size=100), drive(id="d2", name="b.mp4", size=250)]
        service, _, _ = build(videos, [])
        self.assertEqual(service.plan().upload_bytes, 350)


class ExecuteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)

    def test_uploads_then_trashes_source(self) -> None:
        service, fake_drive, uploader = build([drive()], [])
        report = service.execute(service.plan(), work_dir=self.work)
        self.assertEqual(len(uploader.uploads), 1)
        self.assertEqual(fake_drive.trashed, ["d1"])
        self.assertTrue(report.results[0].source_removed)

    def test_keeps_source_when_asked(self) -> None:
        service, fake_drive, _ = build([drive()], [])
        service.execute(service.plan(), work_dir=self.work, remove_source=False)
        self.assertEqual(fake_drive.trashed, [])

    def test_keeps_source_when_youtube_not_processed(self) -> None:
        service, fake_drive, _ = build([drive()], [], processed=False)
        report = service.execute(service.plan(), work_dir=self.work)
        self.assertEqual(fake_drive.trashed, [])
        self.assertIn("處理", report.results[0].error or "")

    def test_temp_file_is_cleaned_up(self) -> None:
        service, _, _ = build([drive()], [])
        service.execute(service.plan(), work_dir=self.work)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_quota_exhaustion_stops_the_run_and_reports_the_rest(self) -> None:
        videos = [drive(id=f"d{i}", name=f"v{i}.mp4") for i in range(3)]
        service, _, _ = build(videos, [], quota_after=1)
        report = service.execute(service.plan(), work_dir=self.work)
        self.assertTrue(report.stopped_by_quota)
        self.assertEqual(len(report.uploaded), 1)
        self.assertEqual(report.not_attempted, 2)

    def test_limit_caps_the_batch(self) -> None:
        videos = [drive(id=f"d{i}", name=f"v{i}.mp4") for i in range(5)]
        service, _, uploader = build(videos, [])
        report = service.execute(service.plan(), work_dir=self.work, limit=2)
        self.assertEqual(len(uploader.uploads), 2)
        self.assertEqual(report.not_attempted, 0)

    def test_privacy_and_title_are_passed_through(self) -> None:
        service, _, uploader = build([drive(name="我的影片 final.mp4")], [])
        service.execute(service.plan(), work_dir=self.work, privacy="unlisted")
        filename, title, privacy = uploader.uploads[0]
        self.assertEqual(privacy, "unlisted")
        self.assertEqual(title, "我的影片 final")


class PlaylistTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)

    def test_upload_is_filed_into_the_named_playlist(self) -> None:
        playlists = FakePlaylists()
        service, _, _ = build([drive()], [], playlists=playlists)
        service.execute(service.plan(), work_dir=self.work, playlist_title="Google Drive 備份上傳")
        self.assertEqual(playlists.ensured, ["Google Drive 備份上傳"])
        self.assertEqual(playlists.added, [("PL1", "yt1")])

    def test_no_playlist_means_no_playlist_calls(self) -> None:
        playlists = FakePlaylists()
        service, _, _ = build([drive()], [], playlists=playlists)
        service.execute(service.plan(), work_dir=self.work)
        self.assertEqual(playlists.ensured, [])
        self.assertEqual(playlists.added, [])

    def test_playlist_failure_keeps_the_upload_but_warns(self) -> None:
        playlists = FakePlaylists(fail_add=True)
        service, fake_drive, _ = build([drive()], [], playlists=playlists)
        report = service.execute(service.plan(), work_dir=self.work, playlist_title="any")
        self.assertTrue(report.results[0].ok)
        self.assertIsNotNone(report.results[0].warning)
        self.assertEqual(fake_drive.trashed, ["d1"])

    def test_missing_scope_aborts_before_spending_quota(self) -> None:
        playlists = FakePlaylists(fail_ensure=True)
        service, fake_drive, uploader = build([drive()], [], playlists=playlists)
        with self.assertRaises(ApiError):
            service.execute(service.plan(), work_dir=self.work, playlist_title="any")
        self.assertEqual(uploader.uploads, [])
        self.assertEqual(fake_drive.downloaded, [])


class QuotaTests(unittest.TestCase):
    def test_default_project_can_upload_six_a_day(self) -> None:
        self.assertEqual(uploads_per_day(), 6)
        self.assertEqual(uploads_per_day(62_400), 39)


if __name__ == "__main__":
    unittest.main()
