"""Console presenter: everything the user reads on stdout.

No decision-making here — the tables and counts are rendered from a ScanResult
that the application layer already computed.
"""

from __future__ import annotations

import sys
from typing import Sequence, TextIO

from ..application.backup_service import BackupPlan, BackupReport
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
        self._progress_bucket: int | None = None

    def _write(self, text: str = "") -> None:
        if self._progress_bucket is not None:
            print(file=self._out)  # finish an in-place progress line first
            self._progress_bucket = None
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

    # -- backup ---------------------------------------------------------

    def backup_plan(self, plan: BackupPlan, *, quota_uploads: int) -> None:
        self._write()
        self._write("--- 備份計畫 ---")
        self._write(
            f"可上傳            {len(plan.uploadable)} 支（{format_size(plan.upload_bytes)}）"
        )
        self._write(f"已在 YouTube      {len(plan.already_on_youtube)} 支（跳過）")
        if plan.needs_review:
            self._write(
                f"疑似已在 YouTube  {len(plan.needs_review)} 支（預設跳過，--include-review 可含）"
            )
        self._write(f"別人分享的        {plan.not_owned} 支（不是你的檔案，不處理）")
        self._write(
            f"每日配額          {quota_uploads} 支（1600 單位/支，預設 10000 單位/日）"
        )

        if plan.uploadable:
            self._write()
            self._write(f"前 {min(20, len(plan.uploadable))} 支:")
            for video in plan.uploadable[:20]:
                self._write(f"  {video.name[:60]:<62} {format_size(video.size):>9}")

    def dry_run_notice(self) -> None:
        self._write()
        self._write("(--dry-run：沒有上傳任何東西，也沒有刪除任何檔案)")

    def upload_progress(self, fraction: float) -> None:
        bucket = int(fraction * 10)
        if bucket == self._progress_bucket:
            return
        self._progress_bucket = bucket
        print(
            f"    上傳中 {fraction * 100:5.1f}%", end="", file=self._out, flush=True
        )

    def backup_report(self, report: BackupReport) -> None:
        self._write()
        self._write(
            f"完成: 上傳 {len(report.uploaded)} 支"
            f"｜移除 Drive {len(report.removed_from_drive)} 支"
            f"｜失敗 {len(report.failed)} 支"
        )
        if report.stopped_by_quota:
            self._write(
                f"配額用盡，還有 {report.not_attempted} 支沒跑；明天額度重置後再執行即可"
            )
        for result in report.results:
            if result.youtube_id:
                self._write(f"  OK   {result.video.name}  ->  https://youtu.be/{result.youtube_id}")
            if result.error:
                self._write(f"  FAIL {result.video.name}: {result.error}")

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

