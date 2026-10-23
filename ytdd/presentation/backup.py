"""`yt-drive-dedup backup`: copy Drive videos to YouTube and reclaim the space.

Uploading costs 1600 quota units per video, so this command is deliberately
loud about the plan before it spends any of it.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from ..application.backup_service import DEFAULT_PROCESS_TIMEOUT_S, BackupService, uploads_per_day
from ..errors import YtddError
from ..infrastructure.credentials import (
    READ_SCOPES,
    WRITE_SCOPES,
    load_credentials,
    resolve_token_path,
)
from ..infrastructure.drive_repository import GoogleDriveRepository
from ..infrastructure.youtube_repository import GoogleYouTubeRepository
from .console import ConsoleReporter

PRIVACY_CHOICES = ("private", "unlisted", "public")
DEFAULT_PLAYLIST = "Google Drive 備份上傳"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-drive-dedup backup",
        description="把 Drive 的影片上傳到 YouTube，成功後再釋放 Drive 空間",
    )
    parser.add_argument("--token", metavar="PATH", help="Google OAuth token JSON 路徑")
    parser.add_argument("--limit", type=int, metavar="N", help="這次最多上傳幾支")
    parser.add_argument(
        "--privacy",
        choices=PRIVACY_CHOICES,
        default="private",
        help="上傳後的隱私狀態（預設 private）",
    )
    parser.add_argument(
        "--keep-drive", action="store_true", help="上傳後保留 Drive 原檔（預設移到垃圾桶）"
    )
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="連 REVIEW（疑似已在 YouTube）的檔案也上傳，可能產生重複",
    )
    parser.add_argument(
        "--playlist",
        metavar="NAME",
        default=DEFAULT_PLAYLIST,
        help=f"上傳後加入的播放清單（不存在會自動建立，預設 {DEFAULT_PLAYLIST}）",
    )
    parser.add_argument(
        "--no-playlist", action="store_true", help="不加入任何播放清單"
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出計畫，不上傳也不刪除")
    parser.add_argument("--work-dir", metavar="DIR", help="下載暫存目錄（預設：系統暫存目錄）")
    parser.add_argument(
        "--process-timeout",
        type=float,
        default=DEFAULT_PROCESS_TIMEOUT_S,
        metavar="SEC",
        help="等待 YouTube 轉檔完成的秒數，逾時保留 Drive 原檔",
    )
    return parser


def run(argv: Sequence[str]) -> int:
    args = build_parser().parse_args(argv)
    reporter = ConsoleReporter()

    token_path = resolve_token_path(args.token)
    try:
        # --dry-run only reads, so a read-only token is enough there. Anything
        # else will upload, so fail on the missing scope before printing a plan
        # that looks like it is about to succeed.
        scopes = READ_SCOPES if args.dry_run else WRITE_SCOPES
        credentials = load_credentials(token_path, scopes)
        drive = GoogleDriveRepository(credentials)
        youtube = GoogleYouTubeRepository(credentials)
        service = BackupService(drive, drive, youtube, youtube, drive, playlists=youtube)
        plan = service.plan(include_review=args.include_review)
    except YtddError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    reporter.backup_plan(
        plan,
        quota_uploads=uploads_per_day(),
        playlist=None if args.no_playlist else args.playlist,
    )
    if not plan.uploadable:
        return 0
    if args.dry_run:
        reporter.dry_run_notice()
        return 0

    work_dir = Path(args.work_dir).expanduser() if args.work_dir else _default_work_dir()
    work_dir.mkdir(parents=True, exist_ok=True)

    report = service.execute(
        plan,
        work_dir=work_dir,
        privacy=args.privacy,
        remove_source=not args.keep_drive,
        limit=args.limit,
        process_timeout_s=args.process_timeout,
        playlist_title=None if args.no_playlist else args.playlist,
        on_message=reporter.progress,
        on_progress=reporter.upload_progress,
    )
    reporter.backup_report(report)
    return 0 if not report.failed else 1


def _default_work_dir() -> Path:
    return Path(tempfile.gettempdir()) / "yt-drive-dedup"
