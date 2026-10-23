"""CLI for the one-time OAuth flow.

    python -m ytdd.auth --client-secret /path/client_secret.json
    python -m ytdd.auth --client-secret /path/client_secret.json --code 'http://localhost:1/?code=...'
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ..errors import YtddError
from ..infrastructure.oauth import InstalledAppOAuth, OAuthConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-drive-dedup-auth",
        description="建立 Google OAuth token 給 yt-drive-dedup 使用",
    )
    parser.add_argument("--client-secret", metavar="PATH", help="client_secret_*.json")
    parser.add_argument("--code", help="瀏覽器停在 http://localhost:1 時，把整條網址貼進來")
    parser.add_argument("--out", default="token.json", help="token 輸出路徑（預設: token.json）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.client_secret:
        parser.print_help()
        return 0

    auth = InstalledAppOAuth(
        OAuthConfig(
            client_secret=Path(args.client_secret).expanduser(),
            token_path=Path(args.out).expanduser(),
        )
    )

    try:
        if not args.code:
            print(auth.authorization_url())
            return 0
        token_path = auth.exchange(args.code)
    except YtddError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"OK: token written to {token_path.resolve()}")
    missing = auth.missing_scopes(token_path)
    if missing:
        print("WARNING: 這些權限沒有被授權，工具可能無法執行:")
        for scope in missing:
            print(f"  - {scope}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
