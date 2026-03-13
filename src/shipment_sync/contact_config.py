from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class ContactSyncSettings:
    clickup_api_token: str
    clickup_contacts_list_id: str
    clickup_contacts_list_ids: list[str]
    clickup_include_closed: bool
    clickup_include_archived: bool
    clickup_contact_task_name_as_full_name: bool
    clickup_contact_cf_full_name: str | None
    clickup_contact_cf_first_name: str | None
    clickup_contact_cf_last_name: str | None
    clickup_contact_cf_email: str | None
    clickup_contact_cf_phone: str | None
    clickup_contact_cf_company: str | None
    clickup_contact_cf_title: str | None
    clickup_contact_cf_linkedin: str | None
    clickup_contact_cf_notes: str | None
    clickup_contact_sync_task_comments: bool
    clickup_contact_comments_limit: int
    icloud_apple_id: str
    icloud_app_specific_password: str
    icloud_carddav_url: str
    icloud_addressbook_url: str | None
    icloud_timeout_seconds: int

    @classmethod
    def from_env(cls, *, require_icloud: bool = True) -> "ContactSyncSettings":
        primary_contacts_list = _optional("CLICKUP_CONTACTS_LIST_ID") or _optional("CLICKUP_LIST_ID")
        if not primary_contacts_list:
            raise ValueError("Missing required env var: CLICKUP_CONTACTS_LIST_ID or CLICKUP_LIST_ID")

        contacts_lists = _csv("CLICKUP_CONTACTS_LIST_IDS")
        if primary_contacts_list not in contacts_lists:
            contacts_lists.insert(0, primary_contacts_list)

        icloud_apple_id = _must("ICLOUD_APPLE_ID") if require_icloud else _optional("ICLOUD_APPLE_ID") or ""
        icloud_app_specific_password = (
            _must("ICLOUD_APP_SPECIFIC_PASSWORD")
            if require_icloud
            else _optional("ICLOUD_APP_SPECIFIC_PASSWORD") or ""
        )

        return cls(
            clickup_api_token=_must("CLICKUP_API_TOKEN"),
            clickup_contacts_list_id=primary_contacts_list,
            clickup_contacts_list_ids=contacts_lists,
            clickup_include_closed=_bool("CLICKUP_CONTACTS_INCLUDE_CLOSED", default=False),
            clickup_include_archived=_bool("CLICKUP_CONTACTS_INCLUDE_ARCHIVED", default=False),
            clickup_contact_task_name_as_full_name=_bool("CLICKUP_CONTACT_TASK_NAME_AS_FULL_NAME", default=True),
            clickup_contact_cf_full_name=_optional("CLICKUP_CONTACT_CF_FULL_NAME"),
            clickup_contact_cf_first_name=_optional("CLICKUP_CONTACT_CF_FIRST_NAME"),
            clickup_contact_cf_last_name=_optional("CLICKUP_CONTACT_CF_LAST_NAME"),
            clickup_contact_cf_email=_optional("CLICKUP_CONTACT_CF_EMAIL"),
            clickup_contact_cf_phone=_optional("CLICKUP_CONTACT_CF_PHONE"),
            clickup_contact_cf_company=_optional("CLICKUP_CONTACT_CF_COMPANY"),
            clickup_contact_cf_title=_optional("CLICKUP_CONTACT_CF_TITLE"),
            clickup_contact_cf_linkedin=_optional("CLICKUP_CONTACT_CF_LINKEDIN"),
            clickup_contact_cf_notes=_optional("CLICKUP_CONTACT_CF_NOTES"),
            clickup_contact_sync_task_comments=_bool("CLICKUP_CONTACT_SYNC_TASK_COMMENTS", default=True),
            clickup_contact_comments_limit=_int("CLICKUP_CONTACT_COMMENTS_LIMIT", default=20, min_value=0),
            icloud_apple_id=icloud_apple_id,
            icloud_app_specific_password=icloud_app_specific_password,
            icloud_carddav_url=os.getenv("ICLOUD_CARDDAV_URL", "https://contacts.icloud.com"),
            icloud_addressbook_url=_optional("ICLOUD_ADDRESSBOOK_URL"),
            icloud_timeout_seconds=_int("ICLOUD_TIMEOUT_SECONDS", default=30, min_value=1),
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


def _int(key: str, *, default: int, min_value: int) -> int:
    value = os.getenv(key)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except Exception:
        return default
    return parsed if parsed >= min_value else default
