"""Google Drive adapter.

Implements both drive-side ports (read and remove) on one object, because they
share a single authenticated client. Splitting them at the port boundary keeps
the application layer honest: it can ask for only one capability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from ..domain.models import DriveVideo
from ..errors import ApiError

VIDEO_QUERY = (
    "trashed = false and ("
    "mimeType contains 'video/' or "
    "fileExtension in 'mp4,mov,avi,mkv,m4v,wmv,flv,webm,ts,mts,3gp,mpg,mpeg'"
    ")"
)

FILE_FIELDS = (
    "nextPageToken, files(id,name,size,mimeType,modifiedTime,ownedByMe,"
    "owners(emailAddress),capabilities(canTrash),"
    "videoMediaMetadata(durationMillis,width,height))"
)


class GoogleDriveRepository:
    """Drive videos, read through files().list and removed through files().update/delete."""

    def __init__(self, credentials: Credentials, page_size: int = 1000) -> None:
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._page_size = page_size

    def list_videos(self) -> Sequence[DriveVideo]:
        videos: list[DriveVideo] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.files()
                .list(
                    q=VIDEO_QUERY,
                    fields=FILE_FIELDS,
                    pageSize=self._page_size,
                    pageToken=page_token,
                    orderBy="name",
                )
                .execute()
            )
            videos.extend(self._to_domain(item) for item in response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return videos

    def download_to(self, video_id: str, destination: Path) -> Path:
        """Stream one video to disk in chunks; never buffer the whole file in memory."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = self._service.files().get_media(fileId=video_id)
        try:
            with destination.open("wb") as handle:
                downloader = MediaIoBaseDownload(handle, request, chunksize=16 << 20)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
        except HttpError as exc:
            raise ApiError(f"下載 {video_id} 失敗: {exc}") from exc
        return destination

    def trash(self, video_id: str) -> None:
        self._service.files().update(fileId=video_id, body={"trashed": True}).execute()

    def delete_permanently(self, video_id: str) -> None:
        self._service.files().delete(fileId=video_id).execute()

    @staticmethod
    def _to_domain(item: dict[str, Any]) -> DriveVideo:
        media = item.get("videoMediaMetadata") or {}
        owners = item.get("owners") or []
        duration = media.get("durationMillis")
        size = item.get("size")
        return DriveVideo(
            id=item["id"],
            name=item.get("name", ""),
            size=int(size) if size else None,
            duration_ms=int(duration) if duration else None,
            modified=(item.get("modifiedTime") or "")[:10],
            owner=",".join(o.get("emailAddress", "") for o in owners),
            owned_by_me=bool(item.get("ownedByMe")),
        )
