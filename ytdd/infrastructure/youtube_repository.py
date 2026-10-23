"""YouTube adapter: list everything already uploaded to the authenticated channel."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Sequence

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from ..application.ports import UploadProgress
from ..domain.models import YouTubeVideo
from ..errors import ApiError, QuotaExceeded

_DURATION_PATTERN = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")

# fileDetails is only returned for videos the caller owns — exactly our case.
VIDEO_FIELDS = "snippet,contentDetails,fileDetails,status"

DEFAULT_CATEGORY_ID = "22"  # People & Blogs; required by videos.insert
UPLOAD_CHUNK_BYTES = 8 << 20
RETRYABLE_STATUS = {500, 502, 503, 504}
_QUOTA_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "uploadLimitExceeded",
    "youtube.quota",
}
_TERMINAL_STATUS = {"failed", "rejected", "deleted"}


def parse_iso8601_duration(value: str | None) -> int | None:
    """PT1H2M3S -> milliseconds. YouTube reports whole seconds, hence the rounding."""
    if not value:
        return None
    match = _DURATION_PATTERN.fullmatch(value)
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    total_seconds = (
        int(days or 0) * 86_400
        + int(hours or 0) * 3_600
        + int(minutes or 0) * 60
        + float(seconds or 0)
    )
    return int(total_seconds * 1000)


class GoogleYouTubeRepository:
    """Channel uploads, walked through the uploads playlist instead of search()."""

    def __init__(self, credentials: Credentials, page_size: int = 50) -> None:
        self._service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        self._page_size = page_size

    def list_uploads(self) -> Sequence[YouTubeVideo]:
        video_ids = self._list_upload_ids()
        videos: list[YouTubeVideo] = []
        for start in range(0, len(video_ids), 50):
            chunk = video_ids[start : start + 50]
            response = (
                self._service.videos()
                .list(part=VIDEO_FIELDS, id=",".join(chunk))
                .execute()
            )
            videos.extend(self._to_domain(item) for item in response.get("items", []))
        return videos

    # -- upload ---------------------------------------------------------

    def upload(
        self,
        *,
        file_path: Path,
        title: str,
        description: str,
        privacy: str,
        on_progress: UploadProgress | None = None,
    ) -> str:
        """Resumable upload. One call costs 1600 quota units, so failures are retried."""
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": DEFAULT_CATEGORY_ID,
            },
            "status": {
                "privacyStatus": privacy,
                # Required for new uploads; the tool never targets kids' content.
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(
            str(file_path), chunksize=UPLOAD_CHUNK_BYTES, resumable=True, mimetype="video/*"
        )
        request = self._service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        backoff = 2
        while response is None:
            try:
                status, response = request.next_chunk()
            except HttpError as exc:
                if _is_quota_error(exc):
                    raise QuotaExceeded(
                        "YouTube 每日 API 配額用盡（每次上傳 1600 單位，預設每天 10000 單位）"
                    ) from exc
                if exc.resp.status in RETRYABLE_STATUS and backoff <= 64:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise ApiError(f"上傳 {file_path.name} 失敗: {exc}") from exc
            if status and on_progress:
                on_progress(status.progress())

        video_id = response.get("id")
        if not video_id:
            raise ApiError(f"上傳 {file_path.name} 沒有回傳 video id")
        return video_id

    def wait_until_processed(self, video_id: str, timeout_s: float) -> bool:
        """Poll until uploadStatus is 'processed'. False on timeout or a bad status."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            response = (
                self._service.videos()
                .list(part="status,processingDetails", id=video_id)
                .execute()
            )
            items = response.get("items") or []
            if not items:
                return False
            upload_status = (items[0].get("status") or {}).get("uploadStatus", "")
            if upload_status == "processed":
                return True
            if upload_status in _TERMINAL_STATUS:
                return False
            time.sleep(10)
        return False

    def _list_upload_ids(self) -> list[str]:
        channels = self._service.channels().list(part="contentDetails", mine=True).execute()
        items = channels.get("items") or []
        if not items:
            raise ApiError("this Google account has no YouTube channel")
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        video_ids: list[str] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.playlistItems()
                .list(
                    part="contentDetails",
                    playlistId=uploads_playlist,
                    maxResults=self._page_size,
                    pageToken=page_token,
                )
                .execute()
            )
            video_ids.extend(
                item["contentDetails"]["videoId"] for item in response.get("items", [])
            )
            page_token = response.get("nextPageToken")
            if not page_token:
                return video_ids

    @staticmethod
    def _to_domain(item: dict[str, Any]) -> YouTubeVideo:
        details = item.get("fileDetails") or {}
        size = details.get("fileSize")
        return YouTubeVideo(
            id=item["id"],
            title=item["snippet"]["title"],
            duration_ms=parse_iso8601_duration(item["contentDetails"].get("duration")),
            original_filename=details.get("fileName"),
            file_size=int(size) if size else None,
            published_at=item["snippet"].get("publishedAt", ""),
        )


def _is_quota_error(error: HttpError) -> bool:
    if error.resp.status not in (403, 429):
        return False
    try:
        payload = json.loads(error.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return False
    for detail in payload.get("error", {}).get("errors", []):
        if detail.get("reason") in _QUOTA_REASONS:
            return True
    return False
