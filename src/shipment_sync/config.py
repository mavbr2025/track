from dataclasses import dataclass
import os


@dataclass
class Settings:
    clickup_api_token: str | None
    clickup_oauth_access_token: str | None
    clickup_oauth_client_id: str | None
    clickup_oauth_client_secret: str | None
    clickup_oauth_redirect_uri: str | None
    clickup_list_id: str
    clickup_list_ids: list[str]
    clickup_team_id: str | None
    clickup_space_ids: list[str]
    clickup_folder_ids: list[str]
    clickup_discover_from_spaces: bool
    clickup_discover_from_team: bool
    cf_container_no: str
    cf_booking_no: str
    cf_shipping_line: str
    cf_shipment_status: str | None
    cf_status_last_checked: str | None
    clickup_include_closed: bool = False
    clickup_include_archived: bool = False
    clickup_use_task_status: bool = False
    clickup_task_status_on_update: str | None = None
    status_comment_prefix: str = "Shipment update"
    eta_only_mode: bool = True
    recent_moves_limit: int = 0
    shipment_allowed_lines: list[str] | None = None
    shipment_excluded_lines: list[str] | None = None
    shipment_skip_unsupported_lines: bool = True
    shipment_preflight_enabled: bool = True
    shipment_preflight_timeout_seconds: int = 8
    shipment_min_sync_interval_hours: int = 0

    @property
    def clickup_auth_header_value(self) -> str:
        if self.clickup_oauth_access_token:
            return f"Bearer {self.clickup_oauth_access_token}"
        if self.clickup_api_token:
            return self.clickup_api_token
        raise ValueError(
            "Missing ClickUp credentials. Set CLICKUP_OAUTH_ACCESS_TOKEN "
            "or CLICKUP_API_TOKEN."
        )

    @property
    def clickup_auth_mode(self) -> str:
        if self.clickup_oauth_access_token:
            return "oauth"
        if self.clickup_api_token:
            return "personal_token"
        return "missing"

    @classmethod
    def from_env(cls) -> "Settings":
        primary_list_id = _must("CLICKUP_LIST_ID")
        parsed_list_ids = _csv("CLICKUP_LIST_IDS")
        if primary_list_id not in parsed_list_ids:
            parsed_list_ids.insert(0, primary_list_id)

        clickup_api_token = _optional("CLICKUP_API_TOKEN")
        clickup_oauth_access_token = _optional("CLICKUP_OAUTH_ACCESS_TOKEN")
        if not clickup_oauth_access_token and not clickup_api_token:
            raise ValueError(
                "Missing ClickUp credentials. Set CLICKUP_OAUTH_ACCESS_TOKEN "
                "or CLICKUP_API_TOKEN."
            )

        return cls(
            clickup_api_token=clickup_api_token,
            clickup_oauth_access_token=clickup_oauth_access_token,
            clickup_oauth_client_id=_optional("CLICKUP_OAUTH_CLIENT_ID"),
            clickup_oauth_client_secret=_optional("CLICKUP_OAUTH_CLIENT_SECRET"),
            clickup_oauth_redirect_uri=_optional("CLICKUP_OAUTH_REDIRECT_URI"),
            clickup_list_id=primary_list_id,
            clickup_list_ids=parsed_list_ids,
            clickup_team_id=_optional("CLICKUP_TEAM_ID"),
            clickup_space_ids=_csv("CLICKUP_SPACE_IDS"),
            clickup_folder_ids=_csv("CLICKUP_FOLDER_IDS"),
            clickup_discover_from_spaces=_bool("CLICKUP_DISCOVER_LISTS_FROM_SPACES", default=True),
            clickup_discover_from_team=_bool("CLICKUP_DISCOVER_LISTS_FROM_TEAM", default=False),
            cf_container_no=_must("CLICKUP_CF_CONTAINER_NO"),
            cf_booking_no=_must("CLICKUP_CF_BOOKING_NO"),
            cf_shipping_line=_must("CLICKUP_CF_SHIPPING_LINE"),
            cf_shipment_status=_optional("CLICKUP_CF_SHIPMENT_STATUS"),
            cf_status_last_checked=_optional("CLICKUP_CF_STATUS_LAST_CHECKED"),
            clickup_include_closed=_bool("CLICKUP_INCLUDE_CLOSED", default=False),
            clickup_include_archived=_bool("CLICKUP_INCLUDE_ARCHIVED", default=False),
            clickup_use_task_status=_bool("CLICKUP_USE_TASK_STATUS", default=False),
            clickup_task_status_on_update=_optional("CLICKUP_TASK_STATUS_ON_UPDATE"),
            status_comment_prefix=os.getenv("CLICKUP_STATUS_COMMENT_PREFIX", "Shipment update"),
            eta_only_mode=_bool("SHIPMENT_ETA_ONLY", default=True),
            recent_moves_limit=_int("SHIPMENT_RECENT_MOVES_LIMIT", default=0, min_value=0),
            shipment_allowed_lines=_csv_normalized("SHIPMENT_ALLOWED_LINES"),
            shipment_excluded_lines=_csv_normalized("SHIPMENT_EXCLUDED_LINES"),
            shipment_skip_unsupported_lines=_bool("SHIPMENT_SKIP_UNSUPPORTED_LINES", default=True),
            shipment_preflight_enabled=_bool("SHIPMENT_PREFLIGHT_ENABLED", default=True),
            shipment_preflight_timeout_seconds=_int("SHIPMENT_PREFLIGHT_TIMEOUT_SECONDS", default=8, min_value=1),
            shipment_min_sync_interval_hours=_int("SHIPMENT_MIN_SYNC_INTERVAL_HOURS", default=0, min_value=0),
        )


def _must(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required env var: {key}")
    return value


def _optional(key: str) -> str | None:
    value = os.getenv(key)
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _csv(key: str) -> list[str]:
    value = os.getenv(key, "")
    return [x.strip() for x in value.split(",") if x.strip()]


def _csv_normalized(key: str) -> list[str] | None:
    items = [x.strip().lower() for x in _csv(key) if x.strip()]
    if not items:
        return None
    return list(dict.fromkeys(items))


def _bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(key: str, default: int, *, min_value: int = 1) -> int:
    value = os.getenv(key)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except Exception:
        return default
    return parsed if parsed >= min_value else default
