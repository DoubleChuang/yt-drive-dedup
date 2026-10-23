"""Console presenter: everything the user reads on stdout.

No decision-making here — the tables and counts are rendered from a ScanResult
that the application layer already computed.
"""

from __future__ import annotations

import sys
from typing import Sequence, TextIO

from ..application.dedup_service import DeletionOutcome, ScanResult
from ..domain.matching import DEFAULT_POLICY, MatchPolicy, classify, score_pair
from ..domain.models import DriveVideo, Match, YouTubeVideo
from .formatting import format_duration, format_size

_TABLE_HEADER = (
    f"{'#':>3}  {'信心':<4} {'證據':<22} {'權':<2} {'Drive 檔名':<42} "
    f"{'大小':>9} {'時長':>9}  YouTube"
)
_RULE = "-" * 158
_LIST_LIMIT = 40


class ConsoleReporter:
    """Renders a scan, an explanation, and deletion results."""

    def __init__(self, out: TextIO | None = None) -> None:
        self._out = out or sys.stdout

    def _write(self, text: str = "") -> None:
        print(text, file=self._out)

    def progress(self, message: str) -> None:
        self._write(message)

    def report(self, scan: ScanResult, *, show_all: bool = False) -> None:
        self._write()
        self._write(_TABLE_HEADER)
        self._write(_RULE)
        for index, match in enumerate(scan.resolution.accepted, start=1):
            self._write(self._row(index, match))
        self._write(_RULE)
        self._summary(scan)
        if show_all:
            self._lists(scan)

    def explanation(
        self,
        videos: Sequence[DriveVideo],
        uploads: Sequence[YouTubeVideo],
        policy: MatchPolicy = DEFAULT_POLICY,
    ) -> None:
        """For each Drive file, print its closest upload even if it scored too low."""
        self._write()
        self._write("--- 每支影片最接近的 YouTube 候選（含未達門檻，供你判斷）---")
        for video in sorted(videos, key=lambda v: v.name.lower()):
            best = max(
                ((score_pair(video, u), u) for u in uploads),
                key=lambda pair: pair[0].points,
                default=None,
            )
            if best is None:
                continue
            score, upload = best
            tier = classify(score.points, policy)
            label = tier.value if tier else "----"
            self._write(
                f"  [{label:<6}{score.points:>4}] {video.name[:38]:<40} "
                f"{format_size(video.size):>9} {format_duration(video.duration_ms):>9}"
                f"  v.s. {upload.title[:34]:<36} {','.join(score.evidence)}  {upload.url}"
            )

    def deletion_plan(self, matches: Sequence[Match], *, permanent: bool) -> None:
        mode = "永久刪除" if permanent else "移到垃圾桶"
        self._write()
        self._write(f"準備{mode} {len(matches)} 個 Drive 檔案:")
        for match in matches:
            self._write(f"  - {match.drive.name}  ->  {match.youtube.url}")

    def deletion_result(self, outcome: DeletionOutcome) -> None:
        if outcome.skipped_not_owned:
            self._write()
            self._write(
                f"跳過 {len(outcome.skipped_not_owned)} 個別人的檔案"
                "（你沒有刪除權限，需擁有者自行處理）:"
            )
            for match in outcome.skipped_not_owned:
                self._write(f"  - {match.drive.name}  (擁有者: {match.drive.owner or '未知'})")

        self._write()
        if not outcome.failures:
            self._write(f"完成: 成功 {len(outcome.deleted)}, 失敗 0")
            return
        self._write(f"完成: 成功 {len(outcome.deleted)}, 失敗 {len(outcome.failures)}")
        for failure in outcome.failures:
            self._write(f"  {failure.match.drive.name}: {failure.reason}")

    def footer(self, report_path: str) -> None:
        self._write()
        self._write(f"報表: {report_path}")

    def report_only_notice(self) -> None:
        self._write()
        self._write("(報告模式，未刪除任何東西。加 --apply 才會刪 STRONG 那批)")

    # -- pieces ---------------------------------------------------------

    def _row(self, index: int, match: Match) -> str:
        ownership = "我" if match.drive.owned_by_me else "他"
        return (
            f"{index:>3}  {match.tier.value:<4} {match.evidence_text:<22} {ownership:<2} "
            f"{match.drive.name[:40]:<42} "
            f"{format_size(match.drive.size):>9} {format_duration(match.drive.duration_ms):>9}"
            f"  {match.youtube.title[:40]}  {match.youtube.url}"
        )

    def _summary(self, scan: ScanResult) -> None:
        strong, review = scan.strong, scan.review
        deletable = [m for m in strong if m.drive.owned_by_me]
        shared = [m for m in strong if not m.drive.owned_by_me]
        total = sum(m.drive.size or 0 for m in deletable)

        self._write(
            f"STRONG {len(strong)} 筆｜你擁有可刪 {len(deletable)} 筆（{format_size(total)}）"
            f"｜別人分享、你無法刪 {len(shared)} 筆"
        )
        self._write(f"REVIEW {len(review)} 筆（疑似，需你確認）")
        self._write(f"Drive 未配對 {len(scan.resolution.unmatched)} 筆")
        if scan.resolution.ambiguous:
            self._write(f"AMBIG {len(scan.resolution.ambiguous)} 筆（多檔搶同一支影片，已排除不刪）")

    def _lists(self, scan: ScanResult) -> None:
        unmatched = sorted(scan.resolution.unmatched, key=lambda v: v.name.lower())
        if unmatched:
            self._write()
            self._write(f"--- Drive 未配對（前 {min(_LIST_LIMIT, len(unmatched))} / 共 {len(unmatched)}）---")
            for video in unmatched[:_LIST_LIMIT]:
                self._write(f"  {video.name}  ({format_size(video.size)})")

        orphans = sorted(scan.unmatched_uploads, key=lambda v: v.title.lower())
        if orphans:
            self._write()
            self._write(
                f"--- 只在 YouTube、Drive 已無來源"
                f"（前 {min(_LIST_LIMIT, len(orphans))} / 共 {len(orphans)}）---"
            )
            for upload in orphans[:_LIST_LIMIT]:
                self._write(f"  {upload.title}  ({upload.url})")

