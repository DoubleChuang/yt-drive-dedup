"""CSV reports: the machine-readable output, kept separate from the console view."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

from ..application.dedup_service import ScanResult
from ..domain.models import AmbiguousMatch, Match

MATCH_FIELDS: tuple[str, ...] = (
    "drive_id",
    "drive_name",
    "drive_size",
    "drive_dur",
    "drive_modified",
    "owner",
    "connected",
    "yt_id",
    "yt_title",
    "yt_url",
    "yt_filename",
    "yt_dur",
    "confidence",
    "label",
    "evidence",
)

UNMATCHED_FIELDS: tuple[str, ...] = ("kind", "name", "size", "duration_ms", "url_or_id")

KIND_NO_MATCH = "drive_no_match"
KIND_YOUTUBE_ONLY = "youtube_only"
KIND_AMBIGUOUS = "ambiguous"


def write_matches(path: Path, matches: Sequence[Match]) -> None:
    """One row per accepted pair."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_match_row(match) for match in matches)


def write_unmatched(path: Path, scan: ScanResult) -> None:
    """Everything that did not become a deletable pair, tagged by reason."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(UNMATCHED_FIELDS)
        writer.writerows(_unmatched_rows(scan))


def report_paths(directory: str | Path, *, owned_only: bool) -> tuple[Path, Path]:
    """Where the two reports live. `--owned-only` gets its own filename to avoid mixing runs."""
    folder = Path(directory).expanduser()
    suffix = "_owned" if owned_only else ""
    return (
        folder / f"yt_drive_dedup_report{suffix}.csv",
        folder / "yt_drive_dedup_unmatched.csv",
    )


def _match_row(match: Match) -> dict[str, object]:
    return {
        "drive_id": match.drive.id,
        "drive_name": match.drive.name,
        "drive_size": match.drive.size,
        "drive_dur": match.drive.duration_ms,
        "drive_modified": match.drive.modified,
        "owner": match.drive.owner,
        "connected": "yes" if match.drive.owned_by_me else "no",
        "yt_id": match.youtube.id,
        "yt_title": match.youtube.title,
        "yt_url": match.youtube.url,
        "yt_filename": match.youtube.original_filename or "",
        "yt_dur": match.youtube.duration_ms,
        "confidence": match.score,
        "label": match.tier.value,
        "evidence": match.evidence_text,
    }


def _unmatched_rows(scan: ScanResult) -> Iterable[Sequence[object]]:
    for video in scan.resolution.unmatched:
        yield KIND_NO_MATCH, video.name, video.size, video.duration_ms, video.id
    for upload in scan.unmatched_uploads:
        yield KIND_YOUTUBE_ONLY, upload.title, upload.file_size, upload.duration_ms, upload.url
    for item in scan.resolution.ambiguous:
        yield from _ambiguous_rows(item)


def _ambiguous_rows(item: AmbiguousMatch) -> Iterable[Sequence[object]]:
    yield (
        KIND_AMBIGUOUS,
        item.match.drive.name,
        item.match.drive.size,
        item.match.drive.duration_ms,
        item.match.youtube.url,
    )
