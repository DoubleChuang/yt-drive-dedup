#!/usr/bin/env python3
"""One-time Google OAuth setup for yt_drive_dedup.py.

Creates a token JSON usable by yt_drive_dedup.py (scopes: drive + youtube.readonly).
Two steps because the redirect cannot reach a headless machine:

  1) python auth.py --client-secret ~/Downloads/client_secret_xxx.json
     -> prints an authorization URL; open it, approve, then copy the FULL
        redirect URL from the browser address bar (it will fail to load
        http://localhost:1 — that is expected).

  2) python auth.py --code 'http://localhost:1/?code=4/0A...&scope=...'
     -> writes token.json

Token location precedence used by yt_drive_dedup.py:
  --token  >  $GOOGLE_TOKEN_FILE  >  ~/.hermes/google_token.json  >  ./token.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REDIRECT_URI = "http://localhost:1"
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/youtube.readonly",
]
PENDING = Path(".oauth_pending.json")


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def get_auth_url(client_secret: Path) -> str:
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(client_secret), scopes=SCOPES, redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=True,
    )
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    PENDING.write_text(
        json.dumps({"state": state, "code_verifier": flow.code_verifier}), encoding="utf-8"
    )
    return url


def exchange(client_secret: Path, code_or_url: str, out: Path) -> None:
    from google_auth_oauthlib.flow import Flow
    from urllib.parse import parse_qs, urlparse

    if not PENDING.exists():
        die("no pending session — run step 1 (--client-secret) again")

    pending = json.loads(PENDING.read_text(encoding="utf-8"))
    granted = list(SCOPES)
    code = code_or_url
    if code_or_url.startswith("http"):
        params = parse_qs(urlparse(code_or_url).query)
        if "code" not in params:
            die("that URL has no ?code= parameter — copy the whole address bar")
        state = (params.get("state") or [""])[0]
        if state and state != pending["state"]:
            die("state mismatch — the URL is from an older attempt, run step 1 again")
        code = params["code"][0]
        scope_val = (params.get("scope") or [""])[0].strip()
        if scope_val:
            granted = scope_val.split()

    flow = Flow.from_client_secrets_file(
        str(client_secret), scopes=granted, redirect_uri=REDIRECT_URI,
        state=pending["state"], code_verifier=pending["code_verifier"],
    )
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    flow.fetch_token(code=code)

    payload = json.loads(flow.credentials.to_json())
    payload["scopes"] = list(flow.credentials.granted_scopes or granted)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    out.chmod(0o600)
    PENDING.unlink(missing_ok=True)

    missing = [s for s in SCOPES if s not in payload["scopes"]]
    print(f"OK: token written to {out.resolve()}")
    if missing:
        print("WARNING: these scopes were not granted:")
        for s in missing:
            print(f"  - {s}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Google OAuth setup for yt_drive_dedup.py")
    ap.add_argument("--client-secret", metavar="PATH", help="client_secret_*.json from Google Cloud")
    ap.add_argument("--code", help="the full http://localhost:1/?code=... URL (or just the code)")
    ap.add_argument("--out", default="token.json", help="where to write the token (default: token.json)")
    args = ap.parse_args()

    if args.client_secret and not args.code:
        secret = Path(args.client_secret).expanduser()
        if not secret.exists():
            die(f"client secret not found: {secret}")
        print(get_auth_url(secret))
        return

    if args.code:
        if not args.client_secret:
            die("--code also needs --client-secret (same file as step 1)")
        exchange(Path(args.client_secret).expanduser(), args.code, Path(args.out).expanduser())
        return

    ap.print_help()


if __name__ == "__main__":
    main()
