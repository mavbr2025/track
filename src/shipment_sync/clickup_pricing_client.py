from __future__ import annotations

from typing import Any
import re

import requests

from .pricing_sync_config import PricingSyncSettings


class ClickUpPricingClient:
    def __init__(self, settings: PricingSyncSettings):
        self.settings = settings
        self.base_url = "https://api.clickup.com/api/v2"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": settings.clickup_auth_header_value,
                "Content-Type": "application/json",
            }
        )

    def get_task(self, task_ref: str) -> dict[str, Any]:
        task_token = extract_clickup_task_token(task_ref)
        if _looks_like_custom_task_id(task_token):
            if not self.settings.clickup_team_id:
                raise ValueError("CLICKUP_TEAM_ID is required when using ClickUp custom task IDs.")
            return self._fetch_task(task_token, custom_task_ids=True)

        try:
            return self._fetch_task(task_token, custom_task_ids=False)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404 or not self.settings.clickup_team_id:
                raise
        return self._fetch_task(task_token, custom_task_ids=True)

    def list_tasks(self, list_ids: list[str]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for list_id in list_ids:
            page = 0
            while True:
                response = self.session.get(
                    f"{self.base_url}/list/{list_id}/task",
                    params={
                        "archived": "false",
                        "subtasks": "false",
                        "include_closed": "false",
                        "page": str(page),
                    },
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                batch = payload.get("tasks", [])
                if not isinstance(batch, list) or not batch:
                    break
                for task in batch:
                    task_id = str(task.get("id") or "").strip()
                    if task_id and task_id not in seen_ids:
                        tasks.append(task)
                        seen_ids.add(task_id)
                if payload.get("last_page") is True:
                    break
                page += 1
        return tasks

    def update_custom_field(self, task_id: str, field_id: str, value: Any) -> None:
        response = self.session.post(
            f"{self.base_url}/task/{task_id}/field/{field_id}",
            json={"value": value},
            timeout=30,
        )
        response.raise_for_status()

    def _fetch_task(self, task_token: str, *, custom_task_ids: bool) -> dict[str, Any]:
        params: dict[str, str] = {}
        if custom_task_ids:
            params = {
                "custom_task_ids": "true",
                "team_id": str(self.settings.clickup_team_id or "").strip(),
            }
        response = self.session.get(
            f"{self.base_url}/task/{task_token}",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()


def extract_clickup_task_token(task_ref: str) -> str:
    raw = task_ref.strip()
    match = re.search(r"/t/(?:\d+/)?([A-Za-z0-9_-]+)", raw)
    if match:
        return match.group(1)
    return raw


def _looks_like_custom_task_id(task_token: str) -> bool:
    return "-" in task_token
