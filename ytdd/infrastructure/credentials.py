"""Credential discovery and loading.

Google auth is an infrastructure concern: it reads the filesystem, talks to the
token endpoint, and raises our own AuthError instead of calling sys.exit.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from ..errors import AuthError

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
REQUIRED_SCOPES: tuple[str, ...] = (DRIVE_SCOPE, YOUTUBE_SCOPE)

TOKEN_ENV_VAR = "GOOGLE_TOKEN_FILE"


def resolve_token_path(explicit: str | Path | None = None) -> Path:
    """Where the token is expected.

    Precedence: explicit argument, then $GOOGLE_TOKEN_FILE, then the usual
    suspects. The environment variable is returned even when the file is
    missing: a container is configured with /data/token.json and the error
    message must name that path, not some fallback the user never set.
    """
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get(TOKEN_ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()
    candidates = (
        Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "google_token.json",
        Path("token.json"),
    )
    return next((c for c in candidates if c.exists()), candidates[-1])


def load_credentials(token_path: Path | None = None) -> Credentials:
    """Read the token, refresh it if needed, and verify our two scopes are present."""
    path = token_path or resolve_token_path()
    if not path.exists():
        raise AuthError(
            f"no Google token at {path}\n"
            "Create one with auth — local: 'python -m ytdd.auth', "
            "Docker: 'docker compose run --rm auth' (see README.md)"
        )

    try:
        credentials = Credentials.from_authorized_user_file(str(path))
    except (ValueError, KeyError) as exc:
        raise AuthError(f"cannot read token at {path}: {exc}") from exc

    _verify_scopes(credentials, path)
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            raise AuthError(f"token at {path} is invalid and cannot be refreshed — re-authorize")
    return credentials


def _verify_scopes(credentials: Credentials, path: Path) -> None:
    granted = {s.strip() for s in (credentials.scopes or []) if s.strip()}
    if not granted:
        return  # older tokens omit the field; the API call will tell us
    missing = [scope for scope in REQUIRED_SCOPES if scope not in granted]
    if missing:
        raise AuthError(
            f"token at {path} is missing scope(s): {', '.join(missing)}\n"
            "Delete the token and re-authorize."
        )
