from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import re
import sys
from typing import Any

import requests

from .config import Settings
from .date_utils import format_display_date, format_port_local_time
from .models import (
    MovementEvent,
    ShipmentFieldWrite,
    ShipmentRef,
    ShipmentStatus,
    ShipmentUpdatePlan,
    ShipmentWriteResult,
)


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
            current_status_value = (
                _field_text(fields.get(self.settings.cf_shipment_status))
                if self.settings.cf_shipment_status
                else None
            )
            last_checked_at = (
                _parse_last_checked(fields.get(self.settings.cf_status_last_checked))
                if self.settings.cf_status_last_checked
                else None
            )
            track_trace_snapshot_hash = (
                _field_text(fields.get(self.settings.cf_track_trace_snapshot))
                if self.settings.cf_track_trace_snapshot
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
                    current_status_value=current_status_value,
                    track_trace_snapshot_hash=track_trace_snapshot_hash,
                    current_field_values={
                        field_id: field_payload.get("value")
                        for field_id, field_payload in fields.items()
                    },
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

    def plan_shipment_update(self, shipment: ShipmentRef, status: ShipmentStatus) -> ShipmentUpdatePlan:
        now_utc = datetime.now(timezone.utc)
        last_checked_display = now_utc.isoformat(timespec="seconds")
        eta_text = _format_event_time(status.eta_local_text, status.eta_time)
        eta_port_local_text = _format_port_local_time(status.eta_local_text, status.eta_time)
        status_value = f"ETA {eta_text}" if self.settings.eta_only_mode else status.status_text
        source_link = status.source_url or _extract_first_url(status.raw_source)
        snapshot_hash = _compute_snapshot_hash(
            shipment=shipment,
            status=status,
            status_value=status_value,
            eta_text=eta_text,
            eta_port_local_text=eta_port_local_text,
        )

        previous_snapshot_hash = (shipment.track_trace_snapshot_hash or "").strip() or None
        previous_status_value = (shipment.current_status_value or "").strip() or None
        changed = True
        if previous_snapshot_hash and previous_snapshot_hash == snapshot_hash:
            changed = False
        elif not previous_snapshot_hash and previous_status_value and previous_status_value == status_value:
            changed = False

        always_field_updates: list[ShipmentFieldWrite] = []
        candidate_field_updates: list[ShipmentFieldWrite] = []
        if self.settings.cf_shipment_status:
            candidate_field_updates.append(
                ShipmentFieldWrite(
                    field_id=self.settings.cf_shipment_status,
                    value=status_value,
                    field_type="text",
                    label="Shipment status",
                )
            )
        if self.settings.cf_status_last_checked:
            always_field_updates.append(
                ShipmentFieldWrite(
                    field_id=self.settings.cf_status_last_checked,
                    value=now_utc,
                    field_type="datetime",
                    label="Last T&T Update",
                )
            )
        if self.settings.cf_track_trace_snapshot:
            candidate_field_updates.append(
                ShipmentFieldWrite(
                    field_id=self.settings.cf_track_trace_snapshot,
                    value=snapshot_hash,
                    field_type="text",
                    label="Track & Trace snapshot",
                )
            )
        candidate_field_updates.extend(_build_direct_event_field_updates(status=status, settings=self.settings))
        candidate_field_updates = _dedupe_field_updates(candidate_field_updates)
        changed_field_updates = [
            update
            for update in candidate_field_updates
            if _field_value_changed(update, shipment.current_field_values.get(update.field_id))
        ]
        custom_field_updates = _dedupe_field_updates(always_field_updates + changed_field_updates)
        fields_changed = bool(changed_field_updates)

        if self.settings.recent_moves_limit > 0:
            recent_moves = status.recent_moves[: self.settings.recent_moves_limit]
            recent_moves_label = f"Recent moves (last {len(recent_moves)}):"
        else:
            recent_moves = status.recent_moves
            recent_moves_label = f"All moves ({len(recent_moves)}):"

        if self.settings.eta_only_mode:
            comment_lines = [
                f"{self.settings.status_comment_prefix}: ETA update",
                f"ETA (port local time): {eta_port_local_text}",
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
                f"ETA (port local time): {eta_port_local_text}",
            ]
            if source_link:
                comment_lines.append(f"Carrier source: {source_link}")
            if shipment.booking_no:
                comment_lines.append(f"Booking: {shipment.booking_no}")
            if shipment.container_no:
                comment_lines.append(f"Container: {shipment.container_no}")
            if status.location:
                comment_lines.append(f"Location: {status.location}")
            event_port_local_time = _format_port_local_time(
                status.latest_move.event_time_local_text if status.latest_move else None,
                status.event_time,
            )
            if event_port_local_time != "n/a":
                comment_lines.append(f"Event time (port local): {event_port_local_time}")
            elif status.event_time:
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

        if not fields_changed and self.settings.shipment_comment_on_no_change:
            no_change_lines = [
                f"{self.settings.status_comment_prefix}: No change found",
                f"T&T executed on {last_checked_display}",
            ]
            if source_link:
                no_change_lines.append(f"Carrier source: {source_link}")
            comment_text = "\n".join(no_change_lines)
        elif fields_changed:
            comment_text = "\n".join(comment_lines)
        else:
            comment_text = None

        return ShipmentUpdatePlan(
            changed=fields_changed,
            status_value=status_value,
            snapshot_hash=snapshot_hash,
            custom_field_updates=custom_field_updates,
            task_status_update=self.settings.clickup_task_status_on_update
            if fields_changed and self.settings.clickup_use_task_status and self.settings.clickup_task_status_on_update
            else None,
            comment_text=comment_text,
        )

    def update_shipment_status(self, shipment: ShipmentRef, status: ShipmentStatus) -> ShipmentWriteResult:
        plan = self.plan_shipment_update(shipment, status)

        for update in plan.custom_field_updates:
            if update.field_type == "datetime":
                self._set_date_custom_field(shipment.task_id, update.field_id, update.value, include_time=True)
            elif update.field_type == "date":
                self._set_date_custom_field(shipment.task_id, update.field_id, update.value, include_time=False)
            else:
                self._set_custom_field(shipment.task_id, update.field_id, str(update.value))

        if plan.task_status_update:
            try:
                self._set_task_status(shipment.task_id, plan.task_status_update)
            except requests.RequestException as exc:
                print(
                    f"Task status update failed for {shipment.task_id}: {exc}",
                    file=sys.stderr,
                )

        if plan.comment_text:
            self._post_comment(shipment.task_id, plan.comment_text)
        return ShipmentWriteResult(
            changed=plan.changed,
            status_value=plan.status_value,
            snapshot_hash=plan.snapshot_hash,
        )

    def _set_custom_field(self, task_id: str, field_id: str, value: str) -> None:
        url = f"{self.base_url}/task/{task_id}/field/{field_id}"
        response = self.session.post(url, json={"value": value}, timeout=30)
        response.raise_for_status()

    def _set_date_custom_field(self, task_id: str, field_id: str, value: datetime | None, *, include_time: bool) -> None:
        url = f"{self.base_url}/task/{task_id}/field/{field_id}"
        payload: dict[str, Any] = {"value": None if value is None else int(value.timestamp() * 1000)}
        if include_time and value is not None:
            payload["value_options"] = {"time": True}
        response = self.session.post(url, json=payload, timeout=30)
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


def _build_direct_event_field_updates(*, status: ShipmentStatus, settings: Settings) -> list[ShipmentFieldWrite]:
    updates: list[ShipmentFieldWrite] = []

    eta_value = _coerce_display_date(status.eta_local_text, status.eta_time)
    if settings.cf_eta and eta_value is not None:
        updates.append(
            ShipmentFieldWrite(
                field_id=settings.cf_eta,
                value=eta_value,
                field_type="date",
                label="ETA",
            )
        )

    ordered_moves = _order_moves_ascending(status.recent_moves)
    if not ordered_moves:
        return updates

    first_discharge_index = next(
        (idx for idx, move in enumerate(ordered_moves) if _event_code_from_move(move) == "DISC"),
        None,
    )
    pre_discharge_moves = ordered_moves[:first_discharge_index] if first_discharge_index is not None else ordered_moves
    post_discharge_moves = ordered_moves[first_discharge_index:] if first_discharge_index is not None else []

    updates.extend(
        _build_move_field_updates(
            moves=pre_discharge_moves,
            field_specs=[
                ("GTIN", settings.cf_gate_in_full, "Gate-in full", False),
                ("GTOT", settings.cf_gate_out_empty, "Gate out empty", False),
            ],
        )
    )
    etd_move = _pick_etd_move(pre_discharge_moves)
    if settings.cf_etd and etd_move is not None:
        etd_value = _coerce_display_date(etd_move.event_time_local_text, etd_move.event_time)
        if etd_value is not None:
            updates.append(
                ShipmentFieldWrite(
                    field_id=settings.cf_etd,
                    value=etd_value,
                    field_type="date",
                    label="ETD",
                )
            )
    updates.extend(
        _build_move_field_updates(
            moves=post_discharge_moves,
            field_specs=[
                ("DISC", settings.cf_discharge_date, "Discharge date", True),
                ("GTOT", settings.cf_gate_out_delivery, "Gate out delivery", True),
                ("GTIN", settings.cf_gate_in_empty, "Gate in empty", True),
            ],
        )
    )
    return updates


def _build_move_field_updates(
    *,
    moves: list[MovementEvent],
    field_specs: list[tuple[str, str | None, str, bool]],
) -> list[ShipmentFieldWrite]:
    updates: list[ShipmentFieldWrite] = []
    for event_code, field_id, label, actual_only in field_specs:
        if not field_id:
            continue
        move = next((candidate for candidate in moves if _event_code_from_move(candidate) == event_code), None)
        if move is None:
            continue
        if actual_only and not _move_is_actual(move):
            updates.append(
                ShipmentFieldWrite(
                    field_id=field_id,
                    value=None,
                    field_type="date",
                    label=label,
                )
            )
            continue
        date_value = _coerce_display_date(move.event_time_local_text, move.event_time)
        if date_value is None:
            continue
        updates.append(
            ShipmentFieldWrite(
                field_id=field_id,
                value=date_value,
                field_type="date",
                label=label,
            )
        )
    return updates


def _pick_etd_move(moves: list[MovementEvent]) -> MovementEvent | None:
    departures = [move for move in moves if _event_code_from_move(move) == "DEPA"]
    if not departures:
        return None

    origin_location = next(
        (_location_key(move.location) for move in moves if move.event_time is not None and _location_key(move.location)),
        None,
    )
    if origin_location is None:
        origin_location = next((_location_key(move.location) for move in moves if _location_key(move.location)), None)

    if origin_location:
        origin_departures = [
            move for move in departures if _location_key(move.location) == origin_location
        ]
        if origin_departures:
            return origin_departures[-1]

    first_actual_load_index = next(
        (
            idx
            for idx, move in enumerate(moves)
            if _event_code_from_move(move) == "LOAD" and _move_is_actual(move)
        ),
        None,
    )
    if first_actual_load_index is not None:
        load_move = moves[first_actual_load_index]
        later_moves = moves[first_actual_load_index + 1 :]
        same_port_actual = [
            move
            for move in later_moves
            if _event_code_from_move(move) == "DEPA"
            and _move_is_actual(move)
            and _locations_match(move.location, load_move.location)
        ]
        if same_port_actual:
            return same_port_actual[0]

        same_port_any = [
            move
            for move in later_moves
            if _event_code_from_move(move) == "DEPA"
            and _locations_match(move.location, load_move.location)
        ]
        if same_port_any:
            return same_port_any[0]

        later_actual = [
            move
            for move in later_moves
            if _event_code_from_move(move) == "DEPA" and _move_is_actual(move)
        ]
        if later_actual:
            return later_actual[0]

        later_departures = [move for move in later_moves if _event_code_from_move(move) == "DEPA"]
        if later_departures:
            return later_departures[0]

    actual_departures = [move for move in departures if _move_is_actual(move)]
    candidates = actual_departures or departures
    return candidates[-1]


def _locations_match(left: str | None, right: str | None) -> bool:
    left_key = _location_key(left)
    right_key = _location_key(right)
    return bool(left_key and right_key and left_key == right_key)


def _location_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", value.strip().upper())
    return normalized or None


def _dedupe_field_updates(updates: list[ShipmentFieldWrite]) -> list[ShipmentFieldWrite]:
    deduped: dict[str, ShipmentFieldWrite] = {}
    for update in updates:
        deduped[update.field_id] = update
    return list(deduped.values())


def _field_value_changed(update: ShipmentFieldWrite, current_value: Any) -> bool:
    if update.field_type == "date":
        planned_dt = _parse_datetime_value(update.value)
        current_dt = _parse_datetime_value(current_value)
        if planned_dt is None:
            return current_dt is not None
        if current_dt is None:
            return True
        return planned_dt.date() != current_dt.date()

    if update.field_type == "datetime":
        planned_dt = _parse_datetime_value(update.value)
        current_dt = _parse_datetime_value(current_value)
        if planned_dt is None:
            return current_dt is not None
        if current_dt is None:
            return True
        return planned_dt.replace(microsecond=0) != current_dt.replace(microsecond=0)

    planned_text = "" if update.value is None else str(update.value).strip()
    current_text = "" if current_value is None else str(current_value).strip()
    return planned_text != current_text


def _order_moves_ascending(moves: list[MovementEvent]) -> list[MovementEvent]:
    indexed = list(enumerate(moves))
    return [
        move
        for _, move in sorted(
            indexed,
            key=lambda item: (
                item[1].event_time or datetime.max.replace(tzinfo=timezone.utc),
                item[0],
            ),
        )
    ]


def _event_code_from_move(move: MovementEvent | None) -> str | None:
    if move is None or not move.name:
        return None
    match = re.search(r"\(([A-Z]{4})\)\s*$", move.name.strip())
    if match:
        return match.group(1)

    normalized = move.name.strip().lower()
    if "empty container release to shipper" in normalized:
        return "GTOT"
    if "transport departed" in normalized:
        return "DEPA"
    if "container discharged" in normalized:
        return "DISC"
    if "container loaded" in normalized:
        return "LOAD"
    if "gated in" in normalized:
        return "GTIN"
    if "gated out" in normalized:
        return "GTOT"
    return None


def _coerce_display_date(local_text: str | None, event_time: datetime | None) -> datetime | None:
    rendered = _format_event_time(local_text, event_time)
    if rendered == "n/a":
        return None
    parsed = _parse_datetime_value(rendered)
    if parsed is not None:
        return _normalize_date_only(parsed)
    if event_time is not None:
        return _normalize_date_only(event_time)
    return None


def _normalize_date_only(value: datetime) -> datetime:
    normalized = value.astimezone(timezone.utc)
    return normalized.replace(hour=12, minute=0, second=0, microsecond=0)


def _move_is_actual(move: MovementEvent) -> bool:
    return _normalize_event_state(move.event_state) == "actual"


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

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

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


def _format_port_local_time(local_text: str | None, event_time: datetime | None) -> str:
    return format_port_local_time(local_text, event_time)


def _format_move_line(move: MovementEvent | None, *, now_utc: datetime | None = None) -> str | None:
    if move is None:
        return None
    parts: list[str] = []
    if move.name and move.name.strip():
        parts.append(move.name.strip())
    event_time = _format_port_local_time(move.event_time_local_text, move.event_time)
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


def _compute_snapshot_hash(
    *,
    shipment: ShipmentRef,
    status: ShipmentStatus,
    status_value: str,
    eta_text: str,
    eta_port_local_text: str,
) -> str:
    latest_move = status.latest_move
    snapshot = {
        "line": shipment.shipping_line,
        "status_value": status_value,
        "status_text": status.status_text or "",
        "eta_text": eta_text,
        "eta_port_local_text": eta_port_local_text,
        "location": status.location or "",
        "event_time": status.event_time.isoformat() if status.event_time else "",
        "movement_details": status.movement_details or "",
        "latest_move_name": latest_move.name if latest_move else "",
        "latest_move_location": latest_move.location if latest_move else "",
        "latest_move_time": _format_port_local_time(
            latest_move.event_time_local_text if latest_move else None,
            latest_move.event_time if latest_move else None,
        ),
    }
    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
