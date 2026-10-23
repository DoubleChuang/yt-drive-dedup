"""Tests for the pure matching rules. No network, no token, no filesystem."""

from __future__ import annotations

import unittest

from ytdd.domain.matching import (
    DEFAULT_POLICY,
    MatchPolicy,
    Resolution,
    classify,
    enforce_unique_upload,
    normalize,
    score_pair,
)
from ytdd.domain.models import DriveVideo, Match, Tier, YouTubeVideo


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
        original_filename=None,
        file_size=None,
        published_at="2026-01-02T00:00:00Z",
    )
    return YouTubeVideo(**{**defaults, **overrides})


class NormalizeTests(unittest.TestCase):
    def test_strips_extension_copy_suffix_and_noise(self) -> None:
        variants = [
            "2026 大阪之旅 Day2.mp4",
            "2026 大阪之旅 Day2 (1).mp4",
            "2026 大阪之旅 Day2 copy.mp4",
            "2026 大阪之旅 Day2.MOV",
        ]
        keys = {normalize(v) for v in variants}
        self.assertEqual(keys, {"2026大阪之旅day2"})

    def test_keeps_distinct_names_apart(self) -> None:
        self.assertNotEqual(normalize("GX013293.MP4"), normalize("GX013294.MP4"))

    def test_handles_none(self) -> None:
        self.assertEqual(normalize(None), "")


class ScoreTests(unittest.TestCase):
    def test_identical_file_scores_full_marks(self) -> None:
        score = score_pair(
            drive(name="VID_1.mp4", size=5000, duration_ms=12_000),
            upload(title="VID_1", file_size=5000, duration_ms=12_000, original_filename="VID_1.mp4"),
        )
        self.assertEqual(score.points, 100)
        self.assertEqual(score.evidence, ("size=", "filename=", "dur="))

    def test_size_and_duration_without_name_is_strong(self) -> None:
        score = score_pair(
            drive(name="生日影片_final.mp4", size=5000, duration_ms=65_000),
            upload(title="寶貝生日快樂", file_size=5000, duration_ms=65_100),
        )
        self.assertEqual(score.points, 95)
        self.assertEqual(classify(score.points), Tier.STRONG)

    def test_title_similarity_alone_is_not_enough(self) -> None:
        score = score_pair(drive(name="剪輯教學.mp4", duration_ms=300_000), upload(title="剪輯教學"))
        self.assertEqual(score.points, 25)
        self.assertIsNone(classify(score.points))

    def test_duration_alone_is_not_enough(self) -> None:
        """Short clips share a rounded duration constantly; that is not identity."""
        score = score_pair(drive(name="IMG_1234.MOV", duration_ms=9_000), upload(title="1", duration_ms=9_000))
        self.assertEqual(score.points, 40)
        self.assertIsNone(classify(score.points))

    def test_single_character_title_never_contains(self) -> None:
        score = score_pair(
            drive(name="VID_20241020_151010_101.mp4"),
            upload(title="1", duration_ms=1_000),
        )
        self.assertEqual(score.evidence, ())

    def test_long_title_containment_counts(self) -> None:
        score = score_pair(
            drive(name="GX015512.MP4 的副本"), upload(title="GX015512")
        )
        self.assertIn("title~", score.evidence)

    def test_policy_is_injectable(self) -> None:
        strict = MatchPolicy(size_equal=10, strong_threshold=10)
        score = score_pair(drive(), upload(file_size=1_000), policy=strict)
        self.assertEqual(classify(score.points, strict), Tier.STRONG)


class ResolutionTests(unittest.TestCase):
    def test_pairs_are_built_and_sorted_by_confidence(self) -> None:
        videos = [
            drive(id="d1", name="low.mp4", size=10, duration_ms=1_000),
            drive(id="d2", name="high.mp4", size=99, duration_ms=2_000),
        ]
        uploads = [
            upload(id="y1", title="low", file_size=10, duration_ms=1_000),
            upload(id="y2", title="high", file_size=99, duration_ms=2_000),
        ]
        resolution = Resolution.build(videos, uploads)
        self.assertEqual([m.drive.id for m in resolution.accepted], ["d2", "d1"])
        self.assertEqual(resolution.unmatched, ())

    def test_unmatched_files_are_reported(self) -> None:
        resolution = Resolution.build([drive(name="orphan.mov", size=7)], [])
        self.assertEqual(resolution.accepted, ())
        self.assertEqual([v.name for v in resolution.unmatched], ["orphan.mov"])


class UniqueUploadTests(unittest.TestCase):
    def test_two_files_claiming_one_upload_without_proof_both_dropped(self) -> None:
        shared_upload = upload(id="y1", title="temp_video_1", duration_ms=19_000)
        matches = [
            Match(drive(id="d1", name="a.mp4", duration_ms=19_000), shared_upload, 55, Tier.REVIEW, ("title~", "dur=")),
            Match(drive(id="d2", name="b.mp4", duration_ms=19_000), shared_upload, 55, Tier.REVIEW, ("title~", "dur=")),
        ]
        accepted, ambiguous = enforce_unique_upload(matches)
        self.assertEqual(accepted, [])
        self.assertEqual(len(ambiguous), 2)

    def test_size_proof_wins_the_tie(self) -> None:
        shared_upload = upload(id="y1", file_size=5000)
        proven = Match(drive(id="d1"), shared_upload, 55, Tier.REVIEW, ("size=",))
        guess = Match(drive(id="d2"), shared_upload, 55, Tier.REVIEW, ("title~", "dur="))
        accepted, ambiguous = enforce_unique_upload([proven, guess])
        self.assertEqual([m.drive.id for m in accepted], ["d1"])
        self.assertEqual([a.match.drive.id for a in ambiguous], ["d2"])

    def test_distinct_uploads_are_untouched(self) -> None:
        matches = [
            Match(drive(id="d1"), upload(id="y1"), 90, Tier.STRONG, ("size=",)),
            Match(drive(id="d2"), upload(id="y2"), 90, Tier.STRONG, ("size=",)),
        ]
        accepted, ambiguous = enforce_unique_upload(matches)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(ambiguous, [])


if __name__ == "__main__":
    unittest.main()
