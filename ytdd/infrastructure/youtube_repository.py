"""YouTube adapter: list everything already uploaded to the authenticated channel."""

from __future__ import annotations

import re
from typing import Any, Sequence

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..domain.models import YouTubeVideo
from ..errors import ApiError

_DURATION_PATTERN = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")

# fileDetails is only returned for videos the caller owns — exactly our case.
VIDEO_FIELDS = "snippet,contentDetails,fileDetails,status"


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
