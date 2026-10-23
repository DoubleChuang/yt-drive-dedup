"""The OAuth "installed app" flow, split into the two steps a headless host needs.

The flow is infrastructure: it talks to Google, writes files, and knows nothing
about argparse. Pending state lives next to the token so a read-only container
image still works.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import AuthError
from .credentials import REQUIRED_SCOPES

REDIRECT_URI = "http://localhost:1"


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    """Where the client secret lives and where the token should be written."""

    client_secret: Path
    token_path: Path
    scopes: tuple[str, ...] = REQUIRED_SCOPES

    @property
    def pending_path(self) -> Path:
        return self.token_path.parent / ".oauth_pending.json"


class InstalledAppOAuth:
    """Two-step flow: ask for a URL, then exchange the pasted redirect for a token."""

    def __init__(self, config: OAuthConfig) -> None:
        self._config = config

    def authorization_url(self) -> str:
        """Build the consent URL and remember the PKCE verifier for step two."""
        flow = self._build_flow()
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        self._config.pending_path.write_text(
            json.dumps({"state": state, "code_verifier": flow.code_verifier}),
            encoding="utf-8",
        )
        return url

    def exchange(self, code_or_url: str) -> Path:
        """Trade the authorization code for a token file and return its path."""
        pending = self._read_pending()
        code, state = _split_code(code_or_url)
        if state and state != pending["state"]:
            raise AuthError("state mismatch — that URL is from an older attempt, start again")

        flow = self._build_flow(
            scopes=self._granted_scopes(code_or_url),
            state=pending["state"],
            code_verifier=pending["code_verifier"],
        )
        # The consent screen lets the user untick boxes; accept a subset.
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            raise AuthError(f"token exchange failed (code expired?): {exc}") from exc

        token_path = self._config.token_path
        token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(flow.credentials.to_json())
        granted = getattr(flow.credentials, "granted_scopes", None)
        payload["scopes"] = list(granted or self._config.scopes)
        token_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        token_path.chmod(0o600)
        self._config.pending_path.unlink(missing_ok=True)
        return token_path

    def missing_scopes(self, token_path: Path) -> list[str]:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
        granted = set(payload.get("scopes") or [])
        return [scope for scope in self._config.scopes if scope not in granted]

    def _build_flow(self, **overrides):
        from google_auth_oauthlib.flow import Flow

        secret = self._config.client_secret
        if not secret.exists():
            raise AuthError(f"client secret not found: {secret}")
        options = {
            "scopes": list(self._config.scopes),
            "redirect_uri": REDIRECT_URI,
            "autogenerate_code_verifier": True,
        }
        options.update(overrides)
        return Flow.from_client_secrets_file(str(secret), **options)

    def _read_pending(self) -> dict:
        path = self._config.pending_path
        if not path.exists():
            raise AuthError("no pending session — request a fresh authorization URL first")
        return json.loads(path.read_text(encoding="utf-8"))

    def _granted_scopes(self, code_or_url: str) -> list[str]:
        if not code_or_url.startswith("http"):
            return list(self._config.scopes)
        from urllib.parse import parse_qs, urlparse

        scope = (parse_qs(urlparse(code_or_url).query).get("scope") or [""])[0].strip()
        return scope.split() if scope else list(self._config.scopes)


def _split_code(code_or_url: str) -> tuple[str, str | None]:
    """Accept a bare code or the whole redirect URL the browser landed on."""
    if not code_or_url.startswith("http"):
        return code_or_url, None
    from urllib.parse import parse_qs, urlparse

    params = parse_qs(urlparse(code_or_url).query)
    if "code" not in params:
        raise AuthError("that URL has no ?code= parameter — copy the whole address bar")
    return params["code"][0], (params.get("state") or [None])[0]
