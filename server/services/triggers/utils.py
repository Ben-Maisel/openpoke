from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from dateutil import parser as date_parser
from dateutil.rrule import rrulestr
from zoneinfo import ZoneInfo

from ...logging_config import logger


UTC = timezone.utc
DEFAULT_STATUS = "active"
VALID_STATUSES = {"active", "paused", "completed"}
# Zones that are UTC+0 all year. Zones that are merely +0 in winter (Europe/London) are not here.
UTC_ZONE_KEYS = {"UTC", "Etc/UTC", "Etc/Universal", "Universal", "Etc/Zulu", "Zulu",
                 "GMT", "Etc/GMT", "Etc/GMT+0", "Etc/GMT-0", "Etc/GMT0", "GMT0", "Etc/Greenwich", "Greenwich"}


def utc_now() -> datetime:
    """Return the current time in UTC."""

    return datetime.now(UTC)


def to_storage_timestamp(moment: datetime) -> str:
    """Normalize timestamps before writing to SQLite."""

    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_timezone(timezone_name: Optional[str]) -> ZoneInfo:
    """Return a `ZoneInfo` instance, defaulting to UTC on errors."""

    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            logger.warning(
                "unknown timezone provided; defaulting to UTC",
                extra={"timezone": timezone_name},
            )
    return ZoneInfo("UTC")


def normalize_status(status: Optional[str]) -> str:
    """Clamp trigger status to the known set."""

    if not status:
        return DEFAULT_STATUS
    normalized = status.lower()
    if normalized not in VALID_STATUSES:
        logger.warning(
            "invalid status supplied; defaulting to active",
            extra={"status": status},
        )
        return DEFAULT_STATUS
    return normalized


def parse_iso(timestamp: str) -> datetime:
    """Parse an ISO timestamp, defaulting to UTC when timezone is absent."""

    dt = date_parser.isoparse(timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def parse_datetime(timestamp: str, tz: ZoneInfo) -> datetime:
    """Parse a timestamp string into the provided timezone."""

    dt = date_parser.isoparse(timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt


def coerce_start_datetime(
    start_time: Optional[str], tz: ZoneInfo, fallback: datetime
) -> datetime:
    """Return the desired start datetime in the agent's timezone."""

    if start_time:
        return parse_datetime(start_time, tz)
    return fallback.astimezone(tz)


def build_recurrence(
    recurrence_rule: Optional[str],
    start_dt_local: datetime,
    tz: ZoneInfo,
) -> Optional[str]:
    """Embed DTSTART metadata into the supplied RRULE text."""

    if not recurrence_rule:
        return None

    if start_dt_local.tzinfo is None:
        localized_start = start_dt_local.replace(tzinfo=tz)
    else:
        localized_start = start_dt_local.astimezone(tz)

    # Pin to UTC only for zones that are UTC year-round; a zone that is merely +0 today
    # (London in winter) needs its TZID so later occurrences follow its DST changes.
    tz_name = getattr(tz, "key", "UTC")
    if tz_name in UTC_ZONE_KEYS:
        dt_line = f"DTSTART:{localized_start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    else:
        dt_line = f"DTSTART;TZID={tz_name}:{localized_start.strftime('%Y%m%dT%H%M%S')}"

    lines = [segment.strip() for segment in recurrence_rule.strip().splitlines() if segment.strip()]
    filtered = [segment for segment in lines if not segment.upper().startswith("DTSTART")]
    if not filtered:
        raise ValueError("recurrence_rule must contain an RRULE definition")

    if not filtered[0].upper().startswith("RRULE"):
        filtered[0] = f"RRULE:{filtered[0]}"

    return "\n".join([dt_line, *filtered])


def load_rrule(recurrence_text: str):
    """Parse a stored recurrence string into a dateutil rule instance."""

    return rrulestr(recurrence_text)


__all__ = [
    "UTC",
    "DEFAULT_STATUS",
    "VALID_STATUSES",
    "build_recurrence",
    "coerce_start_datetime",
    "load_rrule",
    "normalize_status",
    "parse_datetime",
    "parse_iso",
    "resolve_timezone",
    "to_storage_timestamp",
    "utc_now",
]
