"""Ports: the seams the application layer depends on.

Declared as Protocols so the service never imports Google. Swapping in a fake
for tests, or a second video host later, needs no change to the domain or the
application code.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from ..domain.models import DriveVideo, YouTubeVideo


class DriveVideoReader(Protocol):
    """Reads the video files visible in the user's Drive."""

    def list_videos(self) -> Sequence[DriveVideo]:
        ...


class DriveTrashWriter(Protocol):
    """Removes files from Drive. Both calls are per-file and idempotent enough to retry."""

    def trash(self, video_id: str) -> None:
        ...

    def delete_permanently(self, video_id: str) -> None:
        ...


class YouTubeUploadReader(Protocol):
    """Reads the videos already uploaded to the authenticated channel."""

    def list_uploads(self) -> Sequence[YouTubeVideo]:
        ...
