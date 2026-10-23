"""Ports: the seams the application layer depends on.

Declared as Protocols so the services never import Google. Swapping in a fake
for tests, or a second video host later, needs no change to the domain or the
application code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, Sequence

from ..domain.models import DriveVideo, YouTubeVideo

UploadProgress = Callable[[float], None]


class DriveVideoReader(Protocol):
    """Reads the video files visible in the user's Drive."""

    def list_videos(self) -> Sequence[DriveVideo]:
        ...


class DriveTrashWriter(Protocol):
    """Removes files from Drive. Both calls are per-file and safe to retry."""

    def trash(self, video_id: str) -> None:
        ...

    def delete_permanently(self, video_id: str) -> None:
        ...


class YouTubeUploadReader(Protocol):
    """Reads the videos already uploaded to the authenticated channel."""

    def list_uploads(self) -> Sequence[YouTubeVideo]:
        ...


class DriveFileFetcher(Protocol):
    """Materialises one Drive video locally so it can be uploaded elsewhere."""

    def download_to(self, video_id: str, destination: Path) -> Path:
        ...


class YouTubeUploader(Protocol):
    """Publishes a local file to the channel, then waits for it to be processed.

    Both steps live on one port because they are one lifecycle: an upload that
    never finished processing is not a backup you can delete the source of.
    """

    def upload(
        self,
        *,
        file_path: Path,
        title: str,
        description: str,
        privacy: str,
        on_progress: UploadProgress | None = None,
    ) -> str:
        """Return the new video id, or raise QuotaExceeded when the API says so."""
        ...

    def wait_until_processed(self, video_id: str, timeout_s: float) -> bool:
        """True once YouTube finished transcoding; False if it timed out."""
        ...


class YouTubePlaylistWriter(Protocol):
    """Files uploads into a named playlist so backups are not mixed with uploads."""

    def ensure_playlist(self, title: str) -> str:
        """Return the playlist id, creating the playlist when it does not exist."""
        ...

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        ...
