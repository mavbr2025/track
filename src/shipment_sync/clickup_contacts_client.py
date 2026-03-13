from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

import requests

from .contact_config import ContactSyncSettings
from .contact_models import ContactComment, ContactRecord

_EMAIL_HINTS = {"email", "e-mail", "correo", "correoelectronico", "mail"}
_PHONE_HINTS = {"phone", "telefono", "tel", "mobile", "cell", "celular", "whatsapp"}
_LINKEDIN_HINTS = {"linkedin", "linkedinprofile", "profilelinkedin"}


class ClickUpContactsClient:
    def __init__(self, settings: ContactSyncSettings):
        self.settings = settings
        self.base_url = "https://api.clickup.com/api/v2"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": settings.clickup_api_token,
                "Content-Type": "application/json",
            }
        )

    def list_contacts(self) -> list[ContactRecord]:
        tasks: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for list_id in self.settings.clickup_contacts_list_ids:
            open_tasks = self._fetch_tasks(list_id=list_id, archived=False)
            for task in open_tasks:
                if not self.settings.clickup_include_closed and not _is_open_task(task):
                    continue
                task_id = str(task.get("id") or "")
                if task_id and task_id not in seen_ids:
                    tasks.append(task)
                    seen_ids.add(task_id)

            if self.settings.clickup_include_archived:
                archived_tasks = self._fetch_tasks(list_id=list_id, archived=True)
                for task in archived_tasks:
                    if not self.settings.clickup_include_closed and not _is_open_task(task):
                        continue
                    task_id = str(task.get("id") or "")
                    if task_id and task_id not in seen_ids:
                        tasks.append(task)
                        seen_ids.add(task_id)

        contacts: list[ContactRecord] = []
        for task in tasks:
            task_id = str(task.get("id") or "").strip()
            comments: list[ContactComment] = []
            if task_id and self.settings.clickup_contact_sync_task_comments:
                comments = self._fetch_task_comments(task_id)
            contact = _task_to_contact(task, self.settings, comments=comments)
            if contact is not None:
                contacts.append(contact)
        return contacts

    def list_custom_fields(self) -> list[dict[str, str]]:
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for list_id in self.settings.clickup_contacts_list_ids:
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
            "include_closed": "true" if self.settings.clickup_include_closed else "false",
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
        if isinstance(fields, list):
            return fields
        return []

    def _fetch_task_comments(self, task_id: str) -> list[ContactComment]:
        url = f"{self.base_url}/task/{task_id}/comment"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        raw_comments = payload.get("comments")
        if not isinstance(raw_comments, list):
            return []

        comments: list[ContactComment] = []
        for raw in raw_comments:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("comment_text") or "").strip()
            if not text:
                continue
            user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
            author = str(user.get("username") or "").strip() or None
            created = _parse_clickup_time(raw.get("date"))
            comments.append(
                ContactComment(
                    author=author,
                    created_at_utc=created,
                    text=text,
                )
            )

        comments.sort(key=lambda c: c.created_at_utc or "")
        limit = self.settings.clickup_contact_comments_limit
        if limit > 0 and len(comments) > limit:
            comments = comments[-limit:]
        return comments


def _task_to_contact(
    task: dict[str, Any], settings: ContactSyncSettings, *, comments: list[ContactComment]
) -> ContactRecord | None:
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return None

    task_name = str(task.get("name") or "").strip()
    task_url = _coerce_task_url(task.get("url"))
    custom_fields = task.get("custom_fields")
    fields = custom_fields if isinstance(custom_fields, list) else []

    first_name = _field_text(fields, settings.clickup_contact_cf_first_name)
    last_name = _field_text(fields, settings.clickup_contact_cf_last_name)
    full_name = _field_text(fields, settings.clickup_contact_cf_full_name)
    email = _field_text(fields, settings.clickup_contact_cf_email) or _guess_by_field_name(fields, _EMAIL_HINTS)
    phone = _field_text(fields, settings.clickup_contact_cf_phone) or _guess_by_field_name(fields, _PHONE_HINTS)
    company = _field_text(fields, settings.clickup_contact_cf_company)
    title = _field_text(fields, settings.clickup_contact_cf_title)
    linkedin_url = _field_text(fields, settings.clickup_contact_cf_linkedin) or _guess_by_field_name(
        fields, _LINKEDIN_HINTS
    )
    notes = _compose_notes(
        base_notes=_field_text(fields, settings.clickup_contact_cf_notes),
        linkedin_url=linkedin_url,
        comments=comments,
        task_url=task_url,
    )

    if not full_name and settings.clickup_contact_task_name_as_full_name:
        full_name = task_name

    if not first_name and not last_name and full_name:
        first_name, last_name = _split_name(full_name)

    resolved_full_name = full_name or _join_name(first_name, last_name)
    if not resolved_full_name and not email and not phone:
        return None

    if not resolved_full_name:
        resolved_full_name = email or phone or f"ClickUp Contact {task_id}"

    return ContactRecord(
        task_id=task_id,
        task_name=task_name,
        task_url=task_url,
        first_name=first_name,
        last_name=last_name,
        full_name=resolved_full_name,
        email=email,
        phone=phone,
        company=company,
        title=title,
        linkedin_url=linkedin_url,
        notes=notes,
        comments=comments,
    )


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


def _guess_by_field_name(custom_fields: list[dict[str, Any]], candidates: set[str]) -> str | None:
    for field in custom_fields:
        field_name = str(field.get("name") or "")
        if _normalize_token(field_name) in candidates:
            value = _coerce_field_value(field)
            if value:
                return value
    return None


def _coerce_field_value(field: dict[str, Any]) -> str | None:
    value = field.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (int, float)):
        return str(value)
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
                out.append(str(item))
            elif isinstance(item, dict):
                for key in ("email", "phone", "name", "label", "value"):
                    raw = item.get(key)
                    if isinstance(raw, str) and raw.strip():
                        out.append(raw.strip())
                        break
        if out:
            return ", ".join(out)
    return str(value)


def _is_open_task(task: dict[str, Any]) -> bool:
    if task.get("archived") is True:
        return False
    status_obj = task.get("status") if isinstance(task.get("status"), dict) else {}
    status_type = str(status_obj.get("type") or "").strip().lower()
    return status_type not in {"done", "closed"}


def _split_name(full_name: str) -> tuple[str | None, str | None]:
    cleaned = full_name.strip()
    if not cleaned:
        return None, None
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _join_name(first_name: str | None, last_name: str | None) -> str | None:
    parts = [p for p in [first_name, last_name] if p and p.strip()]
    if not parts:
        return None
    return " ".join(parts)


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _parse_clickup_time(value: Any) -> str | None:
    if value is None:
        return None
    try:
        raw = float(str(value).strip())
    except Exception:
        return None
    if raw <= 0:
        return None
    seconds = raw / 1000.0 if raw > 9_999_999_999 else raw
    try:
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except Exception:
        return None
    return dt.isoformat(timespec="seconds")


def _coerce_task_url(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _compose_notes(
    *,
    base_notes: str | None,
    linkedin_url: str | None,
    comments: list[ContactComment],
    task_url: str | None,
) -> str | None:
    sections: list[str] = []

    if base_notes and base_notes.strip():
        sections.append(base_notes.strip())

    metadata: list[str] = []
    if linkedin_url:
        metadata.append(f"LinkedIn: {linkedin_url}")
    if task_url:
        metadata.append(f"ClickUp Task: {task_url}")
    if metadata:
        sections.append("\n".join(metadata))

    if comments:
        comment_lines = ["ClickUp Comments:"]
        for comment in comments:
            prefix_parts: list[str] = []
            if comment.created_at_utc:
                prefix_parts.append(comment.created_at_utc)
            if comment.author:
                prefix_parts.append(comment.author)
            prefix = " | ".join(prefix_parts)
            if prefix:
                comment_lines.append(f"- [{prefix}] {comment.text}")
            else:
                comment_lines.append(f"- {comment.text}")
        sections.append("\n".join(comment_lines))

    if not sections:
        return None
    return "\n\n".join(sections)
