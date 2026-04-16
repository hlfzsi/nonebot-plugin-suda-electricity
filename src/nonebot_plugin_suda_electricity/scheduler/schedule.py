__all__ = ["compute_initial_check_at", "compute_next_check_at"]

import time

from ..config import APP_CONFIG

SECONDS_PER_HOUR = 60 * 60


def compute_next_check_at(*, from_timestamp: int, interval_hours: int = 8) -> int:
    return int(from_timestamp) + _normalize_interval_hours(interval_hours) * SECONDS_PER_HOUR


def compute_initial_check_at(
    *,
    now: int | None = None,
    interval_hours: int | None = None,
) -> int:
    if now is None:
        now = int(time.time())
    if interval_hours is None:
        interval_hours = APP_CONFIG.suda_scheduler_interval_hours
    return compute_next_check_at(
        from_timestamp=now,
        interval_hours=interval_hours,
    )


def _normalize_interval_hours(interval_hours: int) -> int:
    normalized = int(interval_hours)
    if normalized <= 0:
        raise ValueError("interval_hours must be greater than 0")
    return normalized

