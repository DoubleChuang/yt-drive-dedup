"""Command line entry point and composition root.

This is the only module that knows which concrete adapters exist; everything
below it depends on ports. Swapping Drive for a fake in a test never touches
this file.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from ..application.dedup_service import DedupService, ScanResult
from ..domain.models import Match, Tier
from ..errors import YtddError
from ..infrastructure.credentials import load_credentials, resolve_token_path
from ..infrastructure.drive_repository import GoogleDriveRepository
from ..infrastructure.youtube_repository import GoogleYouTubeRepository
from .console import ConsoleReporter
from .csv_report import report_paths, write_matches, write_unmatched


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-drive-dedup",
        description="找出 Google Drive 裡已上傳到 YouTube 的重複影片並刪除",
    )
    parser.add_argument("--apply", action="store_true", help="真的執行刪除，只處理 STRONG")
    parser.add_argument("--permanent", action="store_true", help="永久刪除，不進垃圾桶")
    parser.add_argument("--ids", metavar="3,7,9", help="指定報表列號刪除，覆蓋自動挑選")
    parser.add_argument("--owned-only", action="store_true", help="只考慮你擁有的檔案")
    parser.add_argument("--explain", action="store_true", help="列出每支影片最接近的候選")
    parser.add_argument("--show-all", action="store_true", help="額外印出未配對清單")
    parser.add_argument("--token", metavar="PATH", help="Google OAuth token JSON 路徑")
    parser.add_argument(
        "--report-dir", metavar="DIR", default=".", help="CSV 報表輸出目錄（預設: 目前目錄）"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reporter = ConsoleReporter()

    try:
        service = _build_service(args)
        scan = service.scan(owned_only=args.owned_only, on_progress=reporter.progress)
    except YtddError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    reporter.report(scan, show_all=args.show_all)
    if args.explain:
        reporter.explanation(scan.drive_videos, scan.youtube_videos)

    matches_path, unmatched_path = report_paths(args.report_dir, owned_only=args.owned_only)
    write_matches(matches_path, scan.resolution.accepted)
    write_unmatched(unmatched_path, scan)
    reporter.footer(str(matches_path))

    if not args.apply:
        reporter.report_only_notice()
        return 0

    try:
        targets = select_targets(scan, args.ids)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if not targets:
        print("沒有你有權刪除的項目。")
        return 0

    reporter.deletion_plan(targets, permanent=args.permanent)
    outcome = service.delete(targets, permanent=args.permanent)
    reporter.deletion_result(outcome)
    return 0 if outcome.ok else 1


def select_targets(scan: ScanResult, ids: str | None) -> list[Match]:
    """Rows are numbered exactly as printed, so --ids refers to what the user saw."""
    accepted = list(scan.resolution.accepted)
    if not ids:
        return [match for match in accepted if match.tier is Tier.STRONG]

    try:
        wanted = {int(part) for part in ids.replace(" ", "").split(",") if part}
    except ValueError as error:
        raise ValueError(f"--ids 只能是逗號分隔的數字，收到 {ids!r}") from error

    out_of_range = sorted(n for n in wanted if n < 1 or n > len(accepted))
    if out_of_range:
        raise ValueError(f"列號超出範圍 {out_of_range}，報表只有 1..{len(accepted)} 列")
    return [accepted[index - 1] for index in sorted(wanted)]


def _build_service(args: argparse.Namespace) -> DedupService:
    credentials = load_credentials(resolve_token_path(args.token))
    drive = GoogleDriveRepository(credentials)
    youtube = GoogleYouTubeRepository(credentials)
    return DedupService(drive, youtube, drive)


if __name__ == "__main__":
    raise SystemExit(main())
