"""Allow `python -m ytdd.auth`; the flow itself lives in infrastructure/oauth.py."""

from .presentation.auth_cli import main

raise SystemExit(main())
