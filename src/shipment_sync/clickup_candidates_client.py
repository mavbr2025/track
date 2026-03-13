from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

from .linkedin_candidates_config import LinkedInCandidateSettings
from .linkedin_candidates_models import CandidateProfile

_LINKEDIN_URL_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/(?:in|pub)/[^\s)]+", re.IGNORECASE)


class ClickUpCandidatesClient:
    def __init__(self, settings: LinkedInCandidateSettings):
        self.settings = settings
        self.base_url = "https://api.clickup.com/api/v2"
        self.session = requests.Session()
        self._field_lookup: dict[str, str] | None = None
        self.session.headers.update(
            {
                "Authorization": settings.clickup_api_token,
                "Content-Type": "application/json",
            }
        )

    def list_custom_fields(self) -> list[dict[str, str]]:
        url = f"{self.base_url}/list/{self.settings.clickup_candidates_list_id}/field"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, list):
            return []
        out: list[dict[str, str]] = []
        for field in raw_fields:
            if not isinstance(field, dict):
                continue
            out.append(
                {
                    "id": str(field.get("id") or "").strip(),
                    "name": str(field.get("name") or "").strip(),
                    "type": str(field.get("type") or "").strip(),
                }
            )
        return [field for field in out if field["id"]]

    def list_existing_candidate_urls(self) -> set[str]:
        existing: set[str] = set()
        for task in self._fetch_tasks():
            for url in self._extract_task_urls(task):
                existing.add(url)
        return existing

    def create_candidate_task(self, candidate: CandidateProfile) -> str:
        create_url = f"{self.base_url}/list/{self.settings.clickup_candidates_list_id}/task"
        payload: dict[str, Any] = {
            "name": _task_name(candidate),
            "description": _task_description(candidate),
        }
        if self.settings.clickup_candidate_task_status:
            payload["status"] = self.settings.clickup_candidate_task_status

        response = self.session.post(create_url, json=payload, timeout=30)
        response.raise_for_status()
        task = response.json()
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            raise ValueError(f"ClickUp task creation did not return a task id for {candidate.linkedin_url}")

        self._set_candidate_fields(task_id, candidate)
        self._post_comment(task_id, _task_comment(candidate))
        return task_id

    def _fetch_tasks(self) -> list[dict[str, Any]]:
        url = f"{self.base_url}/list/{self.settings.clickup_candidates_list_id}/task"
        params = {
            "archived": "false",
            "subtasks": "false",
            "include_closed": "true",
        }
        all_tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            params["page"] = str(page)
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            tasks = payload.get("tasks", [])
            if not isinstance(tasks, list) or not tasks:
                break
            all_tasks.extend([task for task in tasks if isinstance(task, dict)])
            if payload.get("last_page") is True:
                break
            page += 1
        return all_tasks

    def _extract_task_urls(self, task: dict[str, Any]) -> set[str]:
        found: set[str] = set()
        text_candidates: list[str] = []

        for key in ("name", "description", "text_content", "url"):
            raw = task.get(key)
            if isinstance(raw, str) and raw.strip():
                text_candidates.append(raw)

        custom_fields = task.get("custom_fields")
        if isinstance(custom_fields, list):
            for field in custom_fields:
                if not isinstance(field, dict):
                    continue
                value = _coerce_field_text(field)
                if value:
                    text_candidates.append(value)

        for text in text_candidates:
            for match in _LINKEDIN_URL_RE.findall(text):
                normalized = _normalize_linkedin_url(match)
                if normalized:
                    found.add(normalized)
        return found

    def _set_candidate_fields(self, task_id: str, candidate: CandidateProfile) -> None:
        fields: list[tuple[str | None, str | int]] = [
            (self.settings.clickup_candidate_cf_linkedin, candidate.linkedin_url),
            (self.settings.clickup_candidate_cf_role, ", ".join(candidate.job_names)),
            (self.settings.clickup_candidate_cf_location, candidate.location_hint or ""),
            (self.settings.clickup_candidate_cf_match_score, candidate.score),
            (self.settings.clickup_candidate_cf_source_query, " | ".join(candidate.source_queries)),
        ]
        for selector, value in fields:
            field_id = self._resolve_field_id(selector)
            if not field_id:
                continue
            self._set_custom_field(task_id, field_id, value)

    def _set_custom_field(self, task_id: str, field_id: str, value: str | int) -> None:
        url = f"{self.base_url}/task/{task_id}/field/{field_id}"
        response = self.session.post(url, json={"value": value}, timeout=30)
        response.raise_for_status()

    def _post_comment(self, task_id: str, comment_text: str) -> None:
        url = f"{self.base_url}/task/{task_id}/comment"
        response = self.session.post(url, json={"comment_text": comment_text, "notify_all": False}, timeout=30)
        response.raise_for_status()

    def _resolve_field_id(self, selector: str | None) -> str | None:
        if not selector:
            return None
        lookup = self._load_field_lookup()
        selector_clean = selector.strip()
        if not selector_clean:
            return None
        if selector_clean in lookup.values():
            return selector_clean
        return lookup.get(_normalize_token(selector_clean))

    def _load_field_lookup(self) -> dict[str, str]:
        if self._field_lookup is not None:
            return self._field_lookup
        lookup: dict[str, str] = {}
        for field in self.list_custom_fields():
            field_id = field["id"]
            field_name = field["name"]
            lookup[_normalize_token(field_name)] = field_id
        self._field_lookup = lookup
        return lookup


def _task_name(candidate: CandidateProfile) -> str:
    roles = ", ".join(candidate.job_names) if candidate.job_names else "Candidate"
    return f"{candidate.full_name} | {roles}"


def _task_description(candidate: CandidateProfile) -> str:
    lines = [
        f"LinkedIn: {candidate.linkedin_url}",
        f"Matched roles: {', '.join(candidate.job_names) if candidate.job_names else 'n/a'}",
        f"Match score: {candidate.score}",
        f"Detected location: {candidate.location_hint or 'n/a'}",
    ]
    if candidate.headline:
        lines.append(f"Headline: {candidate.headline}")
    if candidate.snippet:
        lines.append(f"Search snippet: {candidate.snippet}")
    if candidate.source_queries:
        lines.append("Source query:")
        lines.extend([f"- {query}" for query in candidate.source_queries])
    return "\n".join(lines)


def _task_comment(candidate: CandidateProfile) -> str:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        "Candidate sourced automatically.\n"
        f"UTC checked at: {checked_at}\n"
        f"LinkedIn: {candidate.linkedin_url}\n"
        f"Score: {candidate.score}"
    )


def _coerce_field_text(field: dict[str, Any]) -> str | None:
    value = field.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("url", "value", "name", "label"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, (int, float)):
                parts.append(str(item))
            elif isinstance(item, dict):
                for key in ("url", "value", "name", "label"):
                    raw = item.get(key)
                    if isinstance(raw, str) and raw.strip():
                        parts.append(raw.strip())
                        break
        if parts:
            return ", ".join(parts)
        return None
    return str(value)


def _normalize_linkedin_url(raw_url: str) -> str | None:
    try:
        parsed = urlparse(raw_url.strip())
    except Exception:
        return None

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "linkedin.com":
        return None

    path = parsed.path.rstrip("/")
    if not path.startswith("/in/") and not path.startswith("/pub/"):
        return None

    normalized = parsed._replace(
        scheme="https",
        netloc="www.linkedin.com",
        path=path,
        params="",
        query="",
        fragment="",
    )
    return urlunparse(normalized)


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())
