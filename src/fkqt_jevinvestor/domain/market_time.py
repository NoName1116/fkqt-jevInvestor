from datetime import UTC, date, datetime, time, timedelta, timezone

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
MARKET_CLOSE = time(15)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return value.astimezone(UTC)


def market_close(decision_date: date) -> datetime:
    return datetime.combine(decision_date, MARKET_CLOSE, tzinfo=CHINA_STANDARD_TIME)


def validate_decision_cutoff(decision_date: date, cutoff: datetime) -> None:
    as_utc(cutoff)
    local_cutoff = cutoff.astimezone(CHINA_STANDARD_TIME)
    if local_cutoff.date() != decision_date or local_cutoff < market_close(decision_date):
        raise ValueError("POINT_IN_TIME_VIOLATION")
