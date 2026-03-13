from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from .ap_config import AccountsPayableSettings
from .ap_models import AccountsPayableInvoice


class ClickUpAccountsPayableClient:
    def __init__(self, settings: AccountsPayableSettings):
        self.settings = settings
        self.base_url = "https://api.clickup.com/api/v2"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": settings.clickup_api_token,
                "Content-Type": "application/json",
            }
        )

    def list_invoices(self, *, query: str | None = None) -> list[AccountsPayableInvoice]:
        tasks: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        query_text = (query or "").strip().lower()

        for list_id in self.settings.clickup_ap_list_ids:
            open_tasks = self._fetch_tasks(list_id=list_id, archived=False)
            for task in open_tasks:
                if not self.settings.clickup_ap_include_closed and _is_closed_task(task):
                    continue
                task_id = str(task.get("id") or "")
                if task_id and task_id not in seen_ids:
                    tasks.append(task)
                    seen_ids.add(task_id)

            if self.settings.clickup_ap_include_archived:
                archived_tasks = self._fetch_tasks(list_id=list_id, archived=True)
                for task in archived_tasks:
                    if not self.settings.clickup_ap_include_closed and _is_closed_task(task):
                        continue
                    task_id = str(task.get("id") or "")
                    if task_id and task_id not in seen_ids:
                        tasks.append(task)
                        seen_ids.add(task_id)

        invoices: list[AccountsPayableInvoice] = []
        for task in tasks:
            invoice = _task_to_invoice(task, self.settings)
            if invoice is None:
                continue
            if query_text and not _matches_query(invoice, query_text):
                continue
            invoices.append(invoice)

        return invoices

    def list_custom_fields(self) -> list[dict[str, str]]:
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for list_id in self.settings.clickup_ap_list_ids:
            for field in self._fetch_list_fields(list_id):
                field_id = str(field.get("id") or "").strip()
                if not field_id or field_id in seen:
                    continue
                seen.add(field_id)
                out.append(
                    {
                        "id": field_id,
                        "name": str(field.get("name") or "").strip(),
                        "type": str(field.get("type") or "").strip(),
                        "list_id": list_id,
                    }
                )
        return out

    def _fetch_tasks(self, *, list_id: str, archived: bool) -> list[dict[str, Any]]:
        url = f"{self.base_url}/list/{list_id}/task"
        params = {
            "archived": "true" if archived else "false",
            "subtasks": "false",
            "include_closed": "true" if self.settings.clickup_ap_include_closed else "false",
        }
        all_tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            params["page"] = str(page)
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("tasks", [])
            if not batch:
                break
            all_tasks.extend(batch)
            if payload.get("last_page") is True:
                break
            page += 1
        return all_tasks

    def _fetch_list_fields(self, list_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/list/{list_id}/field"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        fields = payload.get("fields")
        return [field for field in fields if isinstance(field, dict)] if isinstance(fields, list) else []


def _task_to_invoice(task: dict[str, Any], settings: AccountsPayableSettings) -> AccountsPayableInvoice | None:
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return None

    task_name = str(task.get("name") or "").strip()
    custom_fields = task.get("custom_fields")
    fields = custom_fields if isinstance(custom_fields, list) else []

    invoice_number = _field_text(fields, settings.clickup_ap_cf_invoice_number)
    if not invoice_number and settings.clickup_ap_task_name_as_invoice_number:
        invoice_number = task_name or None

    vendor = _field_text(fields, settings.clickup_ap_cf_vendor)
    amount = _field_text(fields, settings.clickup_ap_cf_amount)
    currency = _field_text(fields, settings.clickup_ap_cf_currency)
    status = _field_text(fields, settings.clickup_ap_cf_status) or _task_status_text(task)
    due_date = _field_datetime(fields, settings.clickup_ap_cf_due_date) or _parse_clickup_time(task.get("due_date"))

    list_obj = task.get("list") if isinstance(task.get("list"), dict) else {}
    list_id = str(list_obj.get("id") or "").strip() or settings.clickup_ap_list_id
    list_name = str(list_obj.get("name") or "").strip() or None

    return AccountsPayableInvoice(
        task_id=task_id,
        task_name=task_name,
        task_url=_coerce_task_url(task.get("url")),
        invoice_number=invoice_number,
        vendor=vendor,
        amount=amount,
        currency=currency,
        status=status,
        due_date=due_date,
        list_id=list_id,
        list_name=list_name,
        is_closed=_is_closed_task(task),
        is_archived=task.get("archived") is True,
    )


def _matches_query(invoice: AccountsPayableInvoice, query: str) -> bool:
    haystack = " ".join(
        [
            invoice.task_name or "",
            invoice.invoice_number or "",
            invoice.vendor or "",
            invoice.amount or "",
            invoice.currency or "",
            invoice.status or "",
            invoice.list_name or "",
            invoice.list_id or "",
        ]
    ).lower()
    return query in haystack


def _is_open_task(task: dict[str, Any]) -> bool:
    if _is_closed_task(task):
        return False
    if task.get("archived") is True:
        return False
    return True


def _is_closed_task(task: dict[str, Any]) -> bool:
    status_obj = task.get("status") if isinstance(task.get("status"), dict) else {}
    status_type = str(status_obj.get("type") or "").strip().lower()
    return status_type in {"done", "closed"}


def _task_status_text(task: dict[str, Any]) -> str | None:
    status_obj = task.get("status") if isinstance(task.get("status"), dict) else {}
    status_text = status_obj.get("status")
    if isinstance(status_text, str) and status_text.strip():
        return status_text.strip()
    return None


def _field_text(custom_fields: list[dict[str, Any]], selector: str | None) -> str | None:
    if not selector:
        return None
    selector_norm = _normalize_token(selector)
    for field in custom_fields:
        field_id = str(field.get("id") or "").strip()
        field_name = str(field.get("name") or "").strip()
        if selector == field_id or selector_norm == _normalize_token(field_name):
            return _coerce_field_value(field)
    return None


def _field_datetime(custom_fields: list[dict[str, Any]], selector: str | None) -> datetime | None:
    if not selector:
        return None
    selector_norm = _normalize_token(selector)
    for field in custom_fields:
        field_id = str(field.get("id") or "").strip()
        field_name = str(field.get("name") or "").strip()
        if selector == field_id or selector_norm == _normalize_token(field_name):
            return _parse_clickup_time(field.get("value"))
    return None


def _coerce_field_value(field: dict[str, Any]) -> str | None:
    value = field.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (int, float)):
        option_name = _resolve_option_name(field, value)
        return option_name or str(value)
    if isinstance(value, dict):
        for key in ("email", "phone", "name", "label", "value"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, (int, float)):
                option_name = _resolve_option_name(field, item)
                out.append(option_name or str(item))
            elif isinstance(item, dict):
                for key in ("email", "phone", "name", "label", "value"):
                    raw = item.get(key)
                    if isinstance(raw, str) and raw.strip():
                        out.append(raw.strip())
                        break
        return ", ".join(out) if out else None
    return str(value)


def _resolve_option_name(field: dict[str, Any], raw_value: str | int | float) -> str | None:
    type_config = field.get("type_config")
    if not isinstance(type_config, dict):
        return None
    options = type_config.get("options")
    if not isinstance(options, list):
        return None

    value_as_text = str(raw_value)
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("id")
        orderindex = option.get("orderindex")
        if value_as_text == str(option_id) or value_as_text == str(orderindex):
            name = option.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _parse_clickup_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _from_unix_maybe_ms(float(value))
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            try:
                return _from_unix_maybe_ms(float(raw))
            except Exception:
                return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, dict):
        for key in ("value", "date", "timestamp", "time"):
            parsed = _parse_clickup_time(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _from_unix_maybe_ms(raw: float) -> datetime | None:
    if raw <= 0:
        return None
    seconds = raw / 1000.0 if raw > 9_999_999_999 else raw
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except Exception:
        return None


def _normalize_token(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _coerce_task_url(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
