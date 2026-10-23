"""Tests for the use case, driven through fake ports (no Google SDK involved)."""

from __future__ import annotations

import unittest

from ytdd.application.dedup_service import DedupService
from ytdd.domain.models import DriveVideo, Tier, YouTubeVideo


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
    def __init__(self, videos: list[DriveVideo], fail_ids: set[str] | None = None) -> None:
        self.videos = videos
        self.trashed: list[str] = []
        self.deleted: list[str] = []
        self._fail_ids = fail_ids or set()

    def list_videos(self) -> list[DriveVideo]:
        return self.videos

    def trash(self, video_id: str) -> None:
        if video_id in self._fail_ids:
            raise RuntimeError("403 insufficientFilePermissions")
        self.trashed.append(video_id)

    def delete_permanently(self, video_id: str) -> None:
        self.deleted.append(video_id)


class FakeYouTube:
    def __init__(self, videos: list[YouTubeVideo]) -> None:
        self.videos = videos

    def list_uploads(self) -> list[YouTubeVideo]:
        return self.videos


def build(drive_videos, uploads, fail_ids=None) -> tuple[DedupService, FakeDrive]:
    fake_drive = FakeDrive(drive_videos, fail_ids)
    return DedupService(fake_drive, FakeYouTube(uploads), fake_drive), fake_drive


class ScanTests(unittest.TestCase):
    def test_progress_is_reported_in_order(self) -> None:
        service, _ = build([drive()], [upload()])
        messages: list[str] = []
        service.scan(on_progress=messages.append)
        self.assertEqual(
            messages,
            [
                "reading Drive videos ...",
                "  1 video files in Drive",
                "reading YouTube uploads ...",
                "  1 videos on the channel",
            ],
        )

    def test_owned_only_filters_shared_files(self) -> None:
        videos = [drive(id="d1"), drive(id="d2", owned_by_me=False)]
        service, _ = build(videos, [])
        scan = service.scan(owned_only=True)
        self.assertEqual([v.id for v in scan.drive_videos], ["d1"])
        self.assertEqual(scan.drive_total, 1)

    def test_duplicate_is_matched_as_strong(self) -> None:
        service, _ = build([drive()], [upload()])
        scan = service.scan()
        self.assertEqual([m.drive.id for m in scan.strong], ["d1"])
        self.assertEqual(scan.review, ())


class DeleteTests(unittest.TestCase):
    def test_trashes_owned_matches(self) -> None:
        service, fake = build([drive()], [upload()])
        scan = service.scan()
        outcome = service.delete(scan.strong)
        self.assertEqual(fake.trashed, ["d1"])
        self.assertTrue(outcome.ok)

    def test_skips_files_owned_by_someone_else(self) -> None:
        service, fake = build([drive(owned_by_me=False)], [upload()])
        scan = service.scan()
        outcome = service.delete(scan.strong)
        self.assertEqual(fake.trashed, [])
        self.assertEqual([m.drive.id for m in outcome.skipped_not_owned], ["d1"])

    def test_one_failure_does_not_abort_the_batch(self) -> None:
        videos = [drive(id="d1"), drive(id="d2", name="other.mp4", size=2000)]
        uploads = [upload(id="y1"), upload(id="y2", original_filename="other.mp4", file_size=2000, title="other")]
        service, fake = build(videos, uploads, fail_ids={"d1"})
        outcome = service.delete(service.scan().strong)
        self.assertEqual(fake.trashed, ["d2"])
        self.assertFalse(outcome.ok)
        self.assertEqual(len(outcome.failures), 1)

    def test_permanent_delete_uses_the_other_port_method(self) -> None:
        service, fake = build([drive()], [upload()])
        service.delete(service.scan().strong, permanent=True)
        self.assertEqual(fake.deleted, ["d1"])
        self.assertEqual(fake.trashed, [])


if __name__ == "__main__":
    unittest.main()
