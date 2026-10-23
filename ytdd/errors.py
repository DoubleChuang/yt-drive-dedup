"""Errors the tool raises on purpose, so the CLI can print them without a traceback."""

from __future__ import annotations


class YtddError(Exception):
    """Base class for expected, user-facing failures."""


class AuthError(YtddError):
    """No token, unreadable token, or a token missing a required scope."""


class ApiError(YtddError):
    """A Google API call failed in a way the user has to act on."""


class QuotaExceeded(YtddError):
    """YouTube's daily API quota ran out. Nothing is lost; continue tomorrow."""

    def __init__(self, message: str, *, uploaded_this_run: int = 0) -> None:
        super().__init__(message)
        self.uploaded_this_run = uploaded_this_run
