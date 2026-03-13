from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class OneBookingConfigReport:
    configured: bool
    live_ready: bool
    required_items: list[str]
    missing_required_items: list[str]
    recommended_items: list[str]
    missing_recommended_items: list[str]
    notes: list[str]


@dataclass
class OneBookingSettings:
    api_base_url: str
    request_endpoint: str
    confirmation_endpoint: str
    request_page_url: str
    confirmation_page_url: str
    api_key: str | None
    api_key_header: str
    bearer_token: str | None
    confirmation_ref_param: str
    confirmation_type_param: str | None
    timeout_seconds: int
    max_retries: int
    retry_delay_seconds: float

    @classmethod
    def from_env(cls) -> "OneBookingSettings":
        values = _read_values()
        request_endpoint = values["ONE_BOOKING_REQUEST_ENDPOINT"]
        confirmation_endpoint = values["ONE_BOOKING_CONFIRMATION_ENDPOINT"]
        if not request_endpoint:
            raise ValueError("Missing required env var: ONE_BOOKING_REQUEST_ENDPOINT")
        if not confirmation_endpoint:
            raise ValueError("Missing required env var: ONE_BOOKING_CONFIRMATION_ENDPOINT")

        return cls(
            api_base_url=values["ONE_BOOKING_API_BASE_URL"].rstrip("/"),
            request_endpoint=request_endpoint,
            confirmation_endpoint=confirmation_endpoint,
            request_page_url=_join_url(values["ONE_BOOKING_PAGE_BASE_URL"], values["ONE_BOOKING_REQUEST_PAGE_PATH"]),
            confirmation_page_url=_join_url(
                values["ONE_BOOKING_PAGE_BASE_URL"],
                values["ONE_BOOKING_CONFIRMATION_PAGE_PATH"],
            ),
            api_key=values["ONE_BOOKING_API_KEY"],
            api_key_header=values["ONE_BOOKING_API_KEY_HEADER"],
            bearer_token=values["ONE_BOOKING_BEARER_TOKEN"],
            confirmation_ref_param=values["ONE_BOOKING_CONFIRMATION_REF_PARAM"],
            confirmation_type_param=values["ONE_BOOKING_CONFIRMATION_TYPE_PARAM"],
            timeout_seconds=values["ONE_BOOKING_TIMEOUT_SECONDS"],
            max_retries=values["ONE_BOOKING_MAX_RETRIES"],
            retry_delay_seconds=values["ONE_BOOKING_RETRY_DELAY_SECONDS"],
        )

    @classmethod
    def inspect_env(cls) -> OneBookingConfigReport:
        values = _read_values()
        required_items = [
            "ONE_BOOKING_REQUEST_ENDPOINT",
            "ONE_BOOKING_CONFIRMATION_ENDPOINT",
        ]
        recommended_items = [
            "ONE_BOOKING_BEARER_TOKEN or ONE_BOOKING_API_KEY",
        ]
        missing_required_items = [key for key in required_items if not values[key]]
        missing_recommended_items: list[str] = []
        if not values["ONE_BOOKING_BEARER_TOKEN"] and not values["ONE_BOOKING_API_KEY"]:
            missing_recommended_items.append("ONE_BOOKING_BEARER_TOKEN or ONE_BOOKING_API_KEY")

        notes = [
            "Ask ONE for the exact booking request API path.",
            "Ask ONE for the exact booking confirmation or booking status API path.",
            "Ask which auth method is required for those APIs: bearer token, API key, or another scheme.",
            "Ask for the expected confirmation lookup parameters if they differ from reference/referenceType.",
        ]
        if values["ONE_BOOKING_REQUEST_ENDPOINT"] or values["ONE_BOOKING_CONFIRMATION_ENDPOINT"]:
            notes.append("If ONE provides a full URL, you can paste it directly into the endpoint variable.")

        return OneBookingConfigReport(
            configured=not missing_required_items,
            live_ready=not missing_required_items and not missing_recommended_items,
            required_items=required_items,
            missing_required_items=missing_required_items,
            recommended_items=recommended_items,
            missing_recommended_items=missing_recommended_items,
            notes=notes,
        )


def _must(key: str) -> str:
    value = _optional(key)
    if not value:
        raise ValueError(f"Missing required env var: {key}")
    return value


def _optional(key: str) -> str | None:
    value = os.getenv(key)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _int(key: str, *, default: int, min_value: int) -> int:
    value = _optional(key)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= min_value else default


def _float(key: str, *, default: float, min_value: float) -> float:
    value = _optional(key)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed >= min_value else default


def _join_url(base_url: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _read_values() -> dict[str, str | int | float | None]:
    api_base_url = _optional("ONE_BOOKING_API_BASE_URL") or "https://ecomm.one-line.com/api"
    page_base_url = (_optional("ONE_BOOKING_PAGE_BASE_URL") or "https://ecomm.one-line.com/one-ecom").rstrip("/")
    request_page_path = _optional("ONE_BOOKING_REQUEST_PAGE_PATH") or "/booking/booking-request"
    confirmation_page_path = _optional("ONE_BOOKING_CONFIRMATION_PAGE_PATH") or "/booking/booking-confirm-information"

    return {
        "ONE_BOOKING_API_BASE_URL": api_base_url.rstrip("/"),
        "ONE_BOOKING_REQUEST_ENDPOINT": _optional("ONE_BOOKING_REQUEST_ENDPOINT"),
        "ONE_BOOKING_CONFIRMATION_ENDPOINT": _optional("ONE_BOOKING_CONFIRMATION_ENDPOINT"),
        "ONE_BOOKING_PAGE_BASE_URL": page_base_url,
        "ONE_BOOKING_REQUEST_PAGE_PATH": request_page_path,
        "ONE_BOOKING_CONFIRMATION_PAGE_PATH": confirmation_page_path,
        "ONE_BOOKING_API_KEY": _optional("ONE_BOOKING_API_KEY"),
        "ONE_BOOKING_API_KEY_HEADER": _optional("ONE_BOOKING_API_KEY_HEADER") or "X-API-Key",
        "ONE_BOOKING_BEARER_TOKEN": _optional("ONE_BOOKING_BEARER_TOKEN"),
        "ONE_BOOKING_CONFIRMATION_REF_PARAM": _optional("ONE_BOOKING_CONFIRMATION_REF_PARAM") or "reference",
        "ONE_BOOKING_CONFIRMATION_TYPE_PARAM": _optional("ONE_BOOKING_CONFIRMATION_TYPE_PARAM") or "referenceType",
        "ONE_BOOKING_TIMEOUT_SECONDS": _int("ONE_BOOKING_TIMEOUT_SECONDS", default=60, min_value=1),
        "ONE_BOOKING_MAX_RETRIES": _int("ONE_BOOKING_MAX_RETRIES", default=2, min_value=0),
        "ONE_BOOKING_RETRY_DELAY_SECONDS": _float("ONE_BOOKING_RETRY_DELAY_SECONDS", default=2.0, min_value=0.0),
    }
