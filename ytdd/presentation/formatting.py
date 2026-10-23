"""Human-readable formatting of sizes and durations."""

from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_size(size: int | None) -> str:
    """1650000000 -> '1.5GB'."""
    if size is None:
        return "-"
    value = float(int(size))
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return "-"


def format_duration(milliseconds: int | str | None) -> str:
    """184000 -> '0:03:04'. Accepts strings because Drive returns them quoted."""
    if not milliseconds:
        return "-"
    total_seconds = int(int(milliseconds) / 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}"
