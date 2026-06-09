from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class PricingSyncSettings:
    clickup_api_token: str | None
    clickup_oauth_access_token: str | None
    clickup_team_id: str | None
    clickup_shipment_list_id: str | None
    clickup_shipment_list_ids: list[str]
    clickup_pricing_list_id: str | None
    clickup_pricing_list_ids: list[str]
    clickup_pricing_match_field: str
    clickup_pricing_shipment_match_fields: list[str]
    clickup_pricing_quote_match_fields: list[str]
    clickup_pricing_copy_fields: list[str] | None
    clickup_pricing_only_empty_targets: bool
    clickup_pricing_set_quote_number: bool

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

    @classmethod
    def from_env(cls) -> "PricingSyncSettings":
        shipment_lists = _csv("CLICKUP_LIST_IDS")
        primary_shipment_list = _optional("CLICKUP_LIST_ID")
        if primary_shipment_list and primary_shipment_list not in shipment_lists:
            shipment_lists.insert(0, primary_shipment_list)

        pricing_lists = _csv("CLICKUP_PRICING_LIST_IDS")
        primary_pricing_list = _optional("CLICKUP_PRICING_LIST_ID")
        if primary_pricing_list and primary_pricing_list not in pricing_lists:
            pricing_lists.insert(0, primary_pricing_list)

        clickup_api_token = _optional("CLICKUP_API_TOKEN")
        clickup_oauth_access_token = _optional("CLICKUP_OAUTH_ACCESS_TOKEN")
        if not clickup_oauth_access_token and not clickup_api_token:
            raise ValueError(
                "Missing ClickUp credentials. Set CLICKUP_OAUTH_ACCESS_TOKEN "
                "or CLICKUP_API_TOKEN."
            )

        copy_fields = _csv("CLICKUP_PRICING_COPY_FIELDS")
        primary_match_field = os.getenv("CLICKUP_PRICING_MATCH_FIELD", "MTM Quote #").strip() or "MTM Quote #"
        shipment_match_fields = _csv("CLICKUP_PRICING_SHIPMENT_MATCH_FIELDS") or _default_shipment_match_fields(primary_match_field)
        quote_match_fields = _csv("CLICKUP_PRICING_QUOTE_MATCH_FIELDS") or _default_quote_match_fields(primary_match_field)
        return cls(
            clickup_api_token=clickup_api_token,
            clickup_oauth_access_token=clickup_oauth_access_token,
            clickup_team_id=_optional("CLICKUP_TEAM_ID"),
            clickup_shipment_list_id=primary_shipment_list,
            clickup_shipment_list_ids=shipment_lists,
            clickup_pricing_list_id=primary_pricing_list,
            clickup_pricing_list_ids=pricing_lists,
            clickup_pricing_match_field=primary_match_field,
            clickup_pricing_shipment_match_fields=shipment_match_fields,
            clickup_pricing_quote_match_fields=quote_match_fields,
            clickup_pricing_copy_fields=copy_fields or None,
            clickup_pricing_only_empty_targets=_bool("CLICKUP_PRICING_ONLY_EMPTY_TARGETS", default=True),
            clickup_pricing_set_quote_number=_bool("CLICKUP_PRICING_SET_QUOTE_NUMBER", default=True),
        )


def _optional(key: str) -> str | None:
    value = os.getenv(key)
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def _csv(key: str) -> list[str]:
    value = os.getenv(key, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool(key: str, *, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_shipment_match_fields(primary_match_field: str) -> list[str]:
    values: list[str] = []
    for item in [primary_match_field, "MTM Booking", "Booking number/", "Master BL Number/"]:
        cleaned = item.strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values


def _default_quote_match_fields(primary_match_field: str) -> list[str]:
    values = _default_shipment_match_fields(primary_match_field)
    for item in ["Shipment associated"]:
        cleaned = item.strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values
