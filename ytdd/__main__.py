"""Allow `python -m ytdd` without installing the package."""

from .presentation.cli import main

raise SystemExit(main())
