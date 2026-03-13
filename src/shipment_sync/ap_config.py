from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class AccountsPayableSettings:
    clickup_api_token: str
    clickup_ap_list_id: str
    clickup_ap_list_ids: list[str]
    clickup_ap_include_closed: bool
    clickup_ap_include_archived: bool
    clickup_ap_task_name_as_invoice_number: bool
    clickup_ap_cf_invoice_number: str | None
    clickup_ap_cf_vendor: str | None
    clickup_ap_cf_amount: str | None
    clickup_ap_cf_currency: str | None
    clickup_ap_cf_status: str | None
    clickup_ap_cf_due_date: str | None

    @classmethod
    def from_env(cls) -> "AccountsPayableSettings":
        ap_lists = _csv("CLICKUP_AP_LIST_IDS")
        primary_ap_list = _optional("CLICKUP_AP_LIST_ID") or (ap_lists[0] if ap_lists else None)
        if not primary_ap_list:
            raise ValueError("Missing required env var: CLICKUP_AP_LIST_ID or CLICKUP_AP_LIST_IDS")

        if primary_ap_list not in ap_lists:
            ap_lists.insert(0, primary_ap_list)

        return cls(
            clickup_api_token=_must("CLICKUP_API_TOKEN"),
            clickup_ap_list_id=primary_ap_list,
            clickup_ap_list_ids=ap_lists,
            clickup_ap_include_closed=_bool("CLICKUP_AP_INCLUDE_CLOSED", default=False),
            clickup_ap_include_archived=_bool("CLICKUP_AP_INCLUDE_ARCHIVED", default=False),
            clickup_ap_task_name_as_invoice_number=_bool("CLICKUP_AP_TASK_NAME_AS_INVOICE_NUMBER", default=True),
            clickup_ap_cf_invoice_number=_optional("CLICKUP_AP_CF_INVOICE_NUMBER"),
            clickup_ap_cf_vendor=_optional("CLICKUP_AP_CF_VENDOR"),
            clickup_ap_cf_amount=_optional("CLICKUP_AP_CF_AMOUNT"),
            clickup_ap_cf_currency=_optional("CLICKUP_AP_CF_CURRENCY"),
            clickup_ap_cf_status=_optional("CLICKUP_AP_CF_STATUS"),
            clickup_ap_cf_due_date=_optional("CLICKUP_AP_CF_DUE_DATE"),
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
