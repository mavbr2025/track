from __future__ import annotations

from datetime import datetime
import re

from shipment_sync.carriers.common import parse_event_time


def format_display_date(local_text: str | None, event_time: datetime | None) -> str:
    if local_text:
        parsed_from_text = _extract_date_from_text(local_text)
        if parsed_from_text:
            return parsed_from_text
        parsed_fallback = parse_event_time(local_text)
        if parsed_fallback:
            return parsed_fallback.date().isoformat()

    if event_time:
        return event_time.date().isoformat()
    return "n/a"


def format_port_local_time(local_text: str | None, event_time: datetime | None) -> str:
    if local_text:
        parsed_from_text = _extract_datetime_from_text(local_text)
        if parsed_from_text:
            return parsed_from_text

        parsed_date = _extract_date_from_text(local_text)
        if parsed_date:
            return parsed_date

    if event_time:
        return event_time.strftime("%Y-%m-%d %H:%M")
    return "n/a"


def _extract_date_from_text(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None

    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso_match:
        return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"

    ymd_slash_match = re.search(r"(\d{4})/(\d{2})/(\d{2})", text)
    if ymd_slash_match:
        return f"{ymd_slash_match.group(1)}-{ymd_slash_match.group(2)}-{ymd_slash_match.group(3)}"

    dmy_slash_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if dmy_slash_match:
        return f"{dmy_slash_match.group(3)}-{dmy_slash_match.group(2)}-{dmy_slash_match.group(1)}"

    return None


def _extract_datetime_from_text(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None

    ymd_match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})[T\s](\d{2}):(\d{2})", text)
    if ymd_match:
        return (
            f"{ymd_match.group(1)}-{ymd_match.group(2)}-{ymd_match.group(3)} "
            f"{ymd_match.group(4)}:{ymd_match.group(5)}"
        )

    dmy_match = re.search(r"(\d{2})/(\d{2})/(\d{4})[T\s](\d{2}):(\d{2})", text)
    if dmy_match:
        return (
            f"{dmy_match.group(3)}-{dmy_match.group(2)}-{dmy_match.group(1)} "
            f"{dmy_match.group(4)}:{dmy_match.group(5)}"
        )

    return None
