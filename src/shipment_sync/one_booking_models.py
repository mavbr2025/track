from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OneBookingResult:
    status_text: str
    booking_request_no: str | None
    booking_no: str | None
    confirmation_no: str | None
    reference: str | None
    reference_type: str | None
    source_url: str | None
    raw_source: str | None
    raw_response: dict[str, Any]
