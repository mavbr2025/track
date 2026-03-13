from __future__ import annotations

from datetime import datetime, timezone
import re
import sys
from typing import Any

import requests

from .config import Settings
from .date_utils import format_display_date
from .models import MovementEvent, ShipmentRef, ShipmentStatus


class ClickUpClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = "https://api.clickup.com/api/v2"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": settings.clickup_auth_header_value,
                "Content-Type": "application/json",
            }
        )

    def list_shipments(self) -> list[ShipmentRef]:
        target_lists = self._resolve_target_lists()
        total_lists = len(target_lists)
        print(f"Loading ClickUp tasks from {total_lists} list(s)...", file=sys.stderr)

        tasks: list[dict[str, Any]] = []
        seen_task_ids: set[str] = set()

        for idx, list_id in enumerate(target_lists.keys(), start=1):
            print(f"ClickUp list {idx}/{total_lists}: {list_id}", file=sys.stderr)
            open_tasks = self._fetch_tasks(list_id=list_id, archived=False)
            for t in open_tasks:
                if not _is_open_task(t):
                    continue
                tid = str(t.get("id"))
                if tid and tid not in seen_task_ids:
                    tasks.append(t)
                    seen_task_ids.add(tid)

            if self.settings.clickup_include_archived:
                archived_tasks = self._fetch_tasks(list_id=list_id, archived=True)
                for t in archived_tasks:
                    if not _is_open_task(t):
                        continue
                    tid = str(t.get("id"))
                    if tid and tid not in seen_task_ids:
                        tasks.append(t)
                        seen_task_ids.add(tid)

        shipments: list[ShipmentRef] = []
        for task in tasks:
            fields = _field_map(task.get("custom_fields", []))
            shipping_line = _field_text(fields.get(self.settings.cf_shipping_line))
            booking_no = _field_text(fields.get(self.settings.cf_booking_no))
            container_no = _field_text(fields.get(self.settings.cf_container_no))
            last_checked_at = (
                _parse_last_checked(fields.get(self.settings.cf_status_last_checked))
                if self.settings.cf_status_last_checked
                else None
            )

            if not shipping_line:
                continue
            if not booking_no and not container_no:
                continue

            list_obj = task.get("list") if isinstance(task.get("list"), dict) else {}
            list_id = str(list_obj.get("id") or "")
            list_name = list_obj.get("name") if isinstance(list_obj.get("name"), str) else None
            if not list_id:
                list_id = self.settings.clickup_list_id
                list_name = list_name or target_lists.get(list_id)

            shipments.append(
                ShipmentRef(
                    task_id=task["id"],
                    task_name=task.get("name", ""),
                    shipping_line=shipping_line.strip().lower(),
                    booking_no=booking_no,
                    container_no=container_no,
                    list_id=list_id,
                    list_name=list_name,
                    last_checked_at=last_checked_at,
                )
            )
        print(f"ClickUp candidate shipment tasks: {len(shipments)}", file=sys.stderr)
        return shipments

    def _resolve_target_lists(self) -> dict[str, str]:
        target: dict[str, str] = {lid: None for lid in self.settings.clickup_list_ids}

        for folder_id in self.settings.clickup_folder_ids:
            try:
                folder_lists = self._fetch_folder_lists(folder_id)
            except requests.RequestException as exc:
                self._warn_discovery_failure(f"folder {folder_id}", exc)
                continue
            for lst in folder_lists:
                lid = str(lst.get("id") or "")
                if lid:
                    target[lid] = _name_or_none(lst.get("name"))

        discovered_space_ids: list[str] = []
        if self.settings.clickup_discover_from_team and self.settings.clickup_team_id:
            try:
                discovered_space_ids.extend(self._fetch_team_space_ids(self.settings.clickup_team_id))
            except requests.RequestException as exc:
                self._warn_discovery_failure(f"team {self.settings.clickup_team_id}", exc)
        discovered_space_ids.extend(self.settings.clickup_space_ids)
        discovered_space_ids = list(dict.fromkeys([s for s in discovered_space_ids if s]))

        if self.settings.clickup_discover_from_spaces:
            for space_id in discovered_space_ids:
                try:
                    folderless_lists = self._fetch_space_folderless_lists(space_id)
                except requests.RequestException as exc:
                    self._warn_discovery_failure(f"space {space_id}", exc)
                    continue
                for lst in folderless_lists:
                    lid = str(lst.get("id") or "")
                    if lid:
                        target[lid] = _name_or_none(lst.get("name"))
                try:
                    space_folders = self._fetch_space_folders(space_id)
                except requests.RequestException as exc:
                    self._warn_discovery_failure(f"space folders {space_id}", exc)
                    continue
                for folder in space_folders:
                    folder_id = str(folder.get("id") or "")
                    if not folder_id:
                        continue
                    try:
                        folder_lists = self._fetch_folder_lists(folder_id)
                    except requests.RequestException as exc:
                        self._warn_discovery_failure(f"folder {folder_id}", exc)
                        continue
                    for lst in folder_lists:
                        lid = str(lst.get("id") or "")
                        if lid:
                            target[lid] = _name_or_none(lst.get("name"))

        return {k: (v or "") for k, v in target.items()}

    def _warn_discovery_failure(self, scope: str, exc: requests.RequestException) -> None:
        print(
            f"ClickUp discovery skipped for {scope}: {exc}. "
            f"Continuing with explicit list IDs only.",
            file=sys.stderr,
        )


    def _fetch_team_space_ids(self, team_id: str) -> list[str]:
        url = f"{self.base_url}/team/{team_id}/space"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        spaces = response.json().get("spaces", [])
        ids: list[str] = []
        for s in spaces:
            sid = str(s.get("id") or "")
            if sid:
                ids.append(sid)
        return ids

    def _fetch_space_folders(self, space_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/space/{space_id}/folder"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json().get("folders", [])

    def _fetch_folder_lists(self, folder_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/folder/{folder_id}/list"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json().get("lists", [])

    def _fetch_space_folderless_lists(self, space_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/space/{space_id}/list"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json().get("lists", [])

    def _fetch_tasks(self, list_id: str, archived: bool) -> list[dict[str, Any]]:
        url = f"{self.base_url}/list/{list_id}/task"
        base_params = {
            "archived": "true" if archived else "false",
            "subtasks": "true",
            "include_closed": "false",
        }
        all_tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            params = dict(base_params)
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

    def update_shipment_status(self, shipment: ShipmentRef, status: ShipmentStatus) -> None:
        now_utc = datetime.now(timezone.utc)
        last_checked = now_utc.isoformat()
        last_checked_display = now_utc.date().isoformat()
        eta_text = _format_event_time(status.eta_local_text, status.eta_time)
        status_value = f"ETA {eta_text}" if self.settings.eta_only_mode else status.status_text
        source_link = status.source_url or _extract_first_url(status.raw_source)

        if self.settings.cf_shipment_status:
            self._set_custom_field(shipment.task_id, self.settings.cf_shipment_status, status_value)

        if self.settings.clickup_use_task_status and self.settings.clickup_task_status_on_update:
            try:
                self._set_task_status(shipment.task_id, self.settings.clickup_task_status_on_update)
            except requests.RequestException as exc:
                print(
                    f"Task status update failed for {shipment.task_id}: {exc}",
                    file=sys.stderr,
                )

        if self.settings.cf_status_last_checked:
            self._set_custom_field(shipment.task_id, self.settings.cf_status_last_checked, last_checked)

        if self.settings.recent_moves_limit > 0:
            recent_moves = status.recent_moves[: self.settings.recent_moves_limit]
            recent_moves_label = f"Recent moves (last {len(recent_moves)}):"
        else:
            recent_moves = status.recent_moves
            recent_moves_label = f"All moves ({len(recent_moves)}):"

        if self.settings.eta_only_mode:
            comment_lines = [
                f"{self.settings.status_comment_prefix}: ETA update",
                f"ETA (carrier local time): {eta_text}",
                f"Last checked (UTC): {last_checked_display}",
            ]
            if source_link:
                comment_lines.append(f"Carrier source: {source_link}")
            if recent_moves:
                comment_lines.append(recent_moves_label)
                for idx, move in enumerate(recent_moves, start=1):
                    rendered = _format_move_line(move, now_utc=now_utc)
                    if rendered:
                        comment_lines.append(f"{idx}. {rendered}")
        else:
            comment_lines = [
                f"{self.settings.status_comment_prefix}: {status.status_text}",
                f"Line: {shipment.shipping_line}",
                f"Last checked (UTC): {last_checked_display}",
                f"ETA (carrier local time): {eta_text}",
            ]
            if source_link:
                comment_lines.append(f"Carrier source: {source_link}")
            if shipment.booking_no:
                comment_lines.append(f"Booking: {shipment.booking_no}")
            if shipment.container_no:
                comment_lines.append(f"Container: {shipment.container_no}")
            if status.location:
                comment_lines.append(f"Location: {status.location}")
            if status.event_time:
                comment_lines.append(f"Event time (UTC): {status.event_time.isoformat()}")
            if status.movement_details:
                comment_lines.append(f"Last movement details: {status.movement_details}")
            if recent_moves:
                comment_lines.append(recent_moves_label)
                for idx, move in enumerate(recent_moves, start=1):
                    rendered = _format_move_line(move, now_utc=now_utc)
                    if rendered:
                        comment_lines.append(f"{idx}. {rendered}")
            if status.raw_source:
                comment_lines.append(f"Source trace: {status.raw_source}")

        self._post_comment(shipment.task_id, "\n".join(comment_lines))

    def _set_custom_field(self, task_id: str, field_id: str, value: str) -> None:
        url = f"{self.base_url}/task/{task_id}/field/{field_id}"
        response = self.session.post(url, json={"value": value}, timeout=30)
        response.raise_for_status()

    def _set_task_status(self, task_id: str, status_name: str) -> None:
        url = f"{self.base_url}/task/{task_id}"
        response = self.session.put(url, json={"status": status_name}, timeout=30)
        response.raise_for_status()

    def _post_comment(self, task_id: str, comment: str) -> None:
        url = f"{self.base_url}/task/{task_id}/comment"
        response = self.session.post(url, json={"comment_text": comment, "notify_all": False}, timeout=30)
        response.raise_for_status()


def _field_map(custom_fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in custom_fields if "id" in f}


def _is_open_task(task: dict[str, Any]) -> bool:
    status_obj = task.get("status") if isinstance(task.get("status"), dict) else {}
    status_type = str(status_obj.get("type") or "").strip().lower()
    if status_type in {"done", "closed"}:
        return False
    if task.get("archived") is True:
        return False
    return True


def _field_text(field: dict[str, Any] | None) -> str | None:
    if not field:
        return None
    value = field.get("value")
    if value is None:
        return None

    if isinstance(value, str):
        return value

    if isinstance(value, (int, float)):
        option_name = _resolve_option_name(field, value)
        if option_name:
            return option_name
        return str(value)

    if isinstance(value, dict):
        for key in ("name", "label", "value"):
            v = value.get(key)
            if isinstance(v, str):
                return v
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, (str, int, float)):
                option_name = _resolve_option_name(field, item)
                parts.append(option_name or str(item))
            elif isinstance(item, dict):
                for key in ("name", "label", "value"):
                    v = item.get(key)
                    if isinstance(v, str):
                        parts.append(v)
                        break
        if parts:
            return ", ".join(parts)
    return str(value)


def _parse_last_checked(field: dict[str, Any] | None) -> datetime | None:
    if not field:
        return None
    return _parse_datetime_value(field.get("value"))


def _parse_datetime_value(value: Any) -> datetime | None:
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
            parsed = _parse_datetime_value(value.get(key))
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


def _format_event_time(local_text: str | None, event_time: datetime | None) -> str:
    return format_display_date(local_text, event_time)


def _format_move_line(move: MovementEvent | None, *, now_utc: datetime | None = None) -> str | None:
    if move is None:
        return None
    parts: list[str] = []
    if move.name and move.name.strip():
        parts.append(move.name.strip())
    event_time = _format_event_time(move.event_time_local_text, move.event_time)
    if event_time != "n/a":
        parts.append(event_time)
    if move.location and move.location.strip():
        parts.append(move.location.strip())
    state = _effective_event_state(move, now_utc=now_utc)
    if state:
        parts.append(state.upper())
    if not parts:
        return None
    return " | ".join(parts)


def _effective_event_state(move: MovementEvent, *, now_utc: datetime | None) -> str | None:
    baseline = now_utc or datetime.now(timezone.utc)
    if move.event_time is not None:
        return "actual" if move.event_time.date() <= baseline.date() else "estimate"

    state = _normalize_event_state(move.event_state)
    if state == "actual":
        return "actual"
    if state == "estimated":
        return "estimate"
    return None


def _normalize_event_state(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"actual", "act", "completed", "complete", "confirmed"}:
        return "actual"
    if normalized in {"estimated", "estimate", "est", "planned", "plan", "forecast", "expected", "scheduled"}:
        return "estimated"
    return normalized


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


def _name_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_first_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"https?://\S+", value)
    if not match:
        return None
    return match.group(0).rstrip(".,;)")
