from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class TrackTraceConfigReport:
    configured: bool
    live_ready: bool
    required_items: list[str]
    missing_required_items: list[str]
    recommended_items: list[str]
    missing_recommended_items: list[str]
    notes: list[str]


@dataclass
class ApiTriggerSettings:
    trigger_token: str | None
    allow_query_token: bool

    @classmethod
    def from_env(cls) -> "ApiTriggerSettings":
        return cls(
            trigger_token=_optional("SHIPMENT_API_TRIGGER_TOKEN"),
            allow_query_token=_bool("SHIPMENT_API_ALLOW_QUERY_TOKEN", default=False),
        )


def inspect_track_trace_env() -> TrackTraceConfigReport:
    required_items = [
        "CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN",
        "CLICKUP_LIST_ID",
        "CLICKUP_CF_CONTAINER_NO",
        "CLICKUP_CF_BOOKING_NO",
        "CLICKUP_CF_SHIPPING_LINE",
    ]
    recommended_items = [
        "SHIPMENT_API_TRIGGER_TOKEN",
        "SHIPMENT_API_ALLOW_QUERY_TOKEN=false",
        "CLICKUP_CF_STATUS_LAST_CHECKED",
        "CLICKUP_CF_SHIPMENT_STATUS or CLICKUP_USE_TASK_STATUS",
    ]

    missing_required_items: list[str] = []
    if not _optional("CLICKUP_OAUTH_ACCESS_TOKEN") and not _optional("CLICKUP_API_TOKEN"):
        missing_required_items.append("CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN")
    for key in (
        "CLICKUP_LIST_ID",
        "CLICKUP_CF_CONTAINER_NO",
        "CLICKUP_CF_BOOKING_NO",
        "CLICKUP_CF_SHIPPING_LINE",
    ):
        if not _optional(key):
            missing_required_items.append(key)

    missing_recommended_items: list[str] = []
    for key in ("SHIPMENT_API_TRIGGER_TOKEN", "CLICKUP_CF_STATUS_LAST_CHECKED"):
        if not _optional(key):
            missing_recommended_items.append(key)
    if _bool("SHIPMENT_API_ALLOW_QUERY_TOKEN", default=False):
        missing_recommended_items.append("SHIPMENT_API_ALLOW_QUERY_TOKEN=false")
    has_status_destination = bool(_optional("CLICKUP_CF_SHIPMENT_STATUS")) or (
        _bool("CLICKUP_USE_TASK_STATUS", default=False)
    )
    if not has_status_destination:
        missing_recommended_items.append(
            "CLICKUP_CF_SHIPMENT_STATUS or CLICKUP_USE_TASK_STATUS"
        )
    has_trigger_token = bool(_optional("SHIPMENT_API_TRIGGER_TOKEN"))

    notes = [
        "Set SHIPMENT_API_TRIGGER_TOKEN before exposing trigger endpoints publicly; protected endpoints fail closed when it is missing.",
        "Pass trigger tokens in Authorization: Bearer or X-Trigger-Token headers. Query-string tokens are disabled unless SHIPMENT_API_ALLOW_QUERY_TOKEN=true.",
        "Use CLICKUP_CF_STATUS_LAST_CHECKED together with SHIPMENT_MIN_SYNC_INTERVAL_HOURS to avoid unnecessary carrier calls.",
        "Use CLICKUP_CF_SHIPMENT_STATUS for a dedicated ClickUp custom field, or CLICKUP_USE_TASK_STATUS for task-level operational status progression.",
    ]

    return TrackTraceConfigReport(
        configured=not missing_required_items,
        live_ready=not missing_required_items and has_trigger_token,
        required_items=required_items,
        missing_required_items=missing_required_items,
        recommended_items=recommended_items,
        missing_recommended_items=missing_recommended_items,
        notes=notes,
    )


def _optional(key: str) -> str | None:
    value = os.getenv(key)
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def _bool(key: str, *, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
