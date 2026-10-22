"""Small shared helpers: input parsing/formatting and the application error type."""
from datetime import datetime


class AppError(Exception):
    """Raised for any user-facing validation or business-rule failure."""


def parse_date(text):
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise AppError("Date must be in the format YYYY-MM-DD (e.g. 2026-11-03).")


def parse_time(text):
    """Return minutes since midnight for an 'HH:MM' string."""
    try:
        t = datetime.strptime(str(text).strip(), "%H:%M")
    except ValueError:
        raise AppError("Start time must be in 24-hour format HH:MM (e.g. 09:30).")
    return t.hour * 60 + t.minute


def parse_duration(text):
    try:
        value = int(str(text).strip())
    except ValueError:
        raise AppError("Duration must be a whole number of minutes.")
    if value <= 0:
        raise AppError("Duration must be greater than zero.")
    return value


def fmt_minutes(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
