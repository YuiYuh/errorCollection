from datetime import datetime, timedelta, timezone


# Study dates follow the user's local Hong Kong calendar day.
LOCAL_TZ = timezone(timedelta(hours=8))


def day_bounds_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the current Hong Kong calendar day's UTC bounds."""
    current = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    start_local = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc), (start_local + timedelta(days=1)).astimezone(timezone.utc)
