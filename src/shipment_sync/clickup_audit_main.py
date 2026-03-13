from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import requests

from .project_paths import artifacts_output_path

BASE_URL = "https://api.clickup.com/api/v2"
STALE_DAYS = 30
HEALTHY_SPACE_TARGET_MAX = 10
DEFAULT_AUDIT_JSON = str(artifacts_output_path("audit", "clickup_workspace_audit.json"))
DEFAULT_AUDIT_MD = str(artifacts_output_path("audit", "clickup_workspace_audit.md"))


@dataclass(frozen=True)
class SpaceScope:
    id: str
    name: str
    archived: bool
    statuses: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ListAuditScope:
    list_payload: dict[str, Any]
    space: SpaceScope
    folder_payload: dict[str, Any] | None
    effective_workflow_statuses: tuple[str, ...]


class ClickUpAuditClient:
    def __init__(self, token: str, *, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": token,
                "Content-Type": "application/json",
            }
        )

    def list_team_spaces(self, team_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/team/{team_id}/space")
        spaces = payload.get("spaces")
        return [space for space in spaces if isinstance(space, dict)] if isinstance(spaces, list) else []

    def get_space(self, space_id: str) -> dict[str, Any]:
        return self._get(f"/space/{space_id}")

    def list_space_folders(self, space_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/space/{space_id}/folder")
        folders = payload.get("folders")
        return [folder for folder in folders if isinstance(folder, dict)] if isinstance(folders, list) else []

    def list_space_folderless_lists(self, space_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/space/{space_id}/list")
        lists = payload.get("lists")
        return [item for item in lists if isinstance(item, dict)] if isinstance(lists, list) else []

    def list_folder_lists(self, folder_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/folder/{folder_id}/list")
        lists = payload.get("lists")
        return [item for item in lists if isinstance(item, dict)] if isinstance(lists, list) else []

    def list_list_fields(self, list_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/list/{list_id}/field")
        fields = payload.get("fields")
        return [field for field in fields if isinstance(field, dict)] if isinstance(fields, list) else []

    def list_open_tasks(self, list_id: str) -> list[dict[str, Any]]:
        all_tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            payload = self._get(
                f"/list/{list_id}/task",
                params={
                    "archived": "false",
                    "subtasks": "true",
                    "include_closed": "false",
                    "page": str(page),
                },
            )
            batch = payload.get("tasks")
            if not isinstance(batch, list) or not batch:
                break
            all_tasks.extend(task for task in batch if isinstance(task, dict))
            if payload.get("last_page") is True:
                break
            page += 1
        return all_tasks

    def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = self.session.get(f"{BASE_URL}{path}", params=params, timeout=self.timeout_seconds)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip().replace("\n", " ")
            if len(body) > 280:
                body = f"{body[:280]}..."
            raise RuntimeError(
                f"ClickUp API request failed for GET {path}: status={response.status_code}, "
                f"body={body or '<empty>'}"
            ) from exc
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a read-only ClickUp workspace audit.")
    parser.add_argument(
        "--team-id",
        help="ClickUp team/workspace ID. Defaults to CLICKUP_TEAM_ID from env/.env.",
    )
    parser.add_argument(
        "--space-id",
        action="append",
        help="Explicit Space ID(s). If omitted, discovers all spaces from --team-id / CLICKUP_TEAM_ID.",
    )
    parser.add_argument(
        "--output-json",
        default=DEFAULT_AUDIT_JSON,
        help="Path for machine-readable audit JSON output.",
    )
    parser.add_argument(
        "--output-md",
        default=DEFAULT_AUDIT_MD,
        help="Path for human-readable audit Markdown report.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="HTTP timeout for ClickUp API requests (default: 30).",
    )
    parser.add_argument(
        "--include-archived-spaces",
        action="store_true",
        help="Include archived spaces in the audit inventory.",
    )
    args = parser.parse_args()

    load_dotenv()
    token = (os.getenv("CLICKUP_API_TOKEN") or "").strip()
    if not token:
        raise ValueError("Missing ClickUp token. Set CLICKUP_API_TOKEN in .env or the environment.")
    if args.timeout_seconds < 1:
        raise ValueError("--timeout-seconds must be >= 1")

    client = ClickUpAuditClient(token, timeout_seconds=args.timeout_seconds)
    scope = _resolve_scope(client, team_id=(args.team_id or os.getenv("CLICKUP_TEAM_ID")), space_ids=args.space_id)
    if not args.include_archived_spaces:
        scope = [space for space in scope if not space.archived]
    if not scope:
        raise ValueError("No ClickUp spaces resolved for the audit.")

    report = _run_audit(client, scope)

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    output_md.write_text(_render_markdown(report), encoding="utf-8")

    print(f"Audited {report['summary']['spaces_audited']} space(s), {report['summary']['lists_total']} list(s), "
          f"{report['summary']['open_tasks_total']} open task(s).")
    print(f"JSON report: {output_json}")
    print(f"Markdown report: {output_md}")


def _resolve_scope(client: ClickUpAuditClient, *, team_id: str | None, space_ids: list[str] | None) -> list[SpaceScope]:
    scopes: list[SpaceScope] = []
    if space_ids:
        for space_id in space_ids:
            payload = client.get_space(space_id)
            scopes.append(_space_scope_from_payload(payload, fallback_id=space_id))
        return scopes

    if team_id:
        spaces = client.list_team_spaces(team_id)
        return [_space_scope_from_payload(space, fallback_id=str(space.get("id") or "")) for space in spaces]

    env_space_ids = [value.strip() for value in os.getenv("CLICKUP_SPACE_IDS", "").split(",") if value.strip()]
    if env_space_ids:
        for space_id in env_space_ids:
            payload = client.get_space(space_id)
            scopes.append(_space_scope_from_payload(payload, fallback_id=space_id))
    return scopes


def _space_scope_from_payload(payload: dict[str, Any], *, fallback_id: str) -> SpaceScope:
    statuses = payload.get("statuses")
    return SpaceScope(
        id=str(payload.get("id") or fallback_id),
        name=str(payload.get("name") or f"Space {fallback_id}"),
        archived=bool(payload.get("archived")),
        statuses=tuple(status for status in statuses if isinstance(status, dict)) if isinstance(statuses, list) else (),
    )


def _run_audit(client: ClickUpAuditClient, spaces: list[SpaceScope]) -> dict[str, Any]:
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(days=STALE_DAYS)

    errors: list[str] = []
    fields_by_id: dict[str, dict[str, Any]] = {}
    fields_by_norm_name: dict[str, set[str]] = defaultdict(set)
    workflow_status_counter: Counter[str] = Counter()
    workflow_status_type_counter: Counter[str] = Counter()
    task_status_counter: Counter[str] = Counter()
    task_status_bucket_counter: Counter[str] = Counter()
    list_records: list[dict[str, Any]] = []
    space_records_by_id: dict[str, dict[str, Any]] = {}
    list_scopes: list[ListAuditScope] = []

    totals = Counter()
    overdue_buckets = Counter({"1_7_days": 0, "8_30_days": 0, "31_90_days": 0, "91_plus_days": 0})

    for space in spaces:
        folderless_lists_count = 0
        lists_in_folders_count = 0
        folders_count = 0
        space_open_tasks = 0
        space_recent_updates_30d = 0
        space_stale_open_tasks = 0
        space_overdue_open_tasks = 0
        space_ownerless_open_tasks = 0
        space_no_due_date_open_tasks = 0
        space_custom_field_occurrences = 0
        space_list_names: list[str] = []
        space_field_ids: set[str] = set()
        space_workflow_statuses: set[str] = set()

        for status in space.statuses:
            name = str(status.get("status") or "").strip()
            if not name:
                continue
            workflow_status_counter[name] += 1
            workflow_status_type_counter[str(status.get("type") or "unknown")] += 1
            space_workflow_statuses.add(name)

        try:
            folderless_lists = client.list_space_folderless_lists(space.id)
        except Exception as exc:  # pragma: no cover - network failure path
            errors.append(f"space {space.name} ({space.id}) folderless lists: {exc}")
            folderless_lists = []

        try:
            folders = client.list_space_folders(space.id)
        except Exception as exc:  # pragma: no cover - network failure path
            errors.append(f"space {space.name} ({space.id}) folders: {exc}")
            folders = []

        folderless_lists_count = len(folderless_lists)
        folders_count = len(folders)

        space_record = {
            "space_id": space.id,
            "space_name": space.name,
            "archived": space.archived,
            "folders_count": folders_count,
            "folderless_lists_count": folderless_lists_count,
            "lists_in_folders_count": 0,
            "lists_total": 0,
            "mixed_hierarchy": False,
            "open_tasks": 0,
            "recent_updates_30d": 0,
            "stale_open_tasks": 0,
            "overdue_open_tasks": 0,
            "ownerless_open_tasks": 0,
            "no_due_date_open_tasks": 0,
            "custom_field_occurrences": 0,
            "distinct_field_ids": 0,
            "distinct_workflow_statuses": [],
        }
        space_records_by_id[space.id] = space_record

        for list_payload in folderless_lists:
            list_scopes.append(
                ListAuditScope(
                    list_payload=list_payload,
                    space=space,
                    folder_payload=None,
                    effective_workflow_statuses=tuple(sorted(space_workflow_statuses)),
                )
            )

        for folder in folders:
            folder_statuses = folder.get("statuses")
            folder_status_names: set[str] = set()
            if isinstance(folder_statuses, list):
                for status in folder_statuses:
                    if not isinstance(status, dict):
                        continue
                    name = str(status.get("status") or "").strip()
                    if not name:
                        continue
                    folder_status_names.add(name)
                    workflow_status_counter[name] += 1
                    workflow_status_type_counter[str(status.get("type") or "unknown")] += 1
                    space_workflow_statuses.add(name)

            try:
                folder_lists = client.list_folder_lists(str(folder.get("id") or ""))
            except Exception as exc:  # pragma: no cover - network failure path
                errors.append(
                    f"space {space.name} ({space.id}) folder {folder.get('name') or folder.get('id')}: {exc}"
                )
                folder_lists = []

            lists_in_folders_count += len(folder_lists)
            for list_payload in folder_lists:
                effective_names = tuple(sorted(folder_status_names or space_workflow_statuses))
                list_scopes.append(
                    ListAuditScope(
                        list_payload=list_payload,
                        space=space,
                        folder_payload=folder,
                        effective_workflow_statuses=effective_names,
                    )
                )
        space_record["lists_in_folders_count"] = lists_in_folders_count
        space_record["lists_total"] = folderless_lists_count + lists_in_folders_count
        space_record["mixed_hierarchy"] = folderless_lists_count > 0 and lists_in_folders_count > 0
        space_record["distinct_workflow_statuses"] = sorted(space_workflow_statuses)

    max_workers = min(12, max(4, (os.cpu_count() or 4)))
    total_lists = len(list_scopes)
    print(f"Auditing {total_lists} lists with up to {max_workers} concurrent workers...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _audit_list,
                client=client,
                scope=scope,
                stale_cutoff=stale_cutoff,
                now=now,
            )
            for scope in list_scopes
        ]
        for idx, future in enumerate(as_completed(futures), start=1):
            try:
                record, field_rows, counter_rows, error_rows = future.result()
            except Exception as exc:  # pragma: no cover - defensive path
                errors.append(f"list audit worker failure: {exc}")
                continue

            list_records.append(record)
            errors.extend(error_rows)

            for field_id, name, field_type, list_id, space_name in field_rows:
                fields_by_norm_name[_normalize(name)].add(field_id)
                info = fields_by_id.setdefault(
                    field_id,
                    {
                        "name": name,
                        "type": field_type,
                        "lists": set(),
                        "spaces": set(),
                    },
                )
                info["lists"].add(list_id)
                info["spaces"].add(space_name)

            for status_name in counter_rows["task_statuses"]:
                task_status_counter[status_name] += 1
                task_status_bucket_counter[_status_bucket(status_name)] += 1

            totals["open_tasks"] += record["open_tasks"]
            totals["ownerless_open_tasks"] += record["ownerless_open_tasks"]
            totals["multi_assignee_open_tasks"] += record["multi_assignee_open_tasks"]
            totals["no_due_date_open_tasks"] += record["no_due_date_open_tasks"]
            totals["overdue_open_tasks"] += record["overdue_open_tasks"]
            totals["stale_open_tasks"] += record["stale_open_tasks"]
            totals["recent_updates_30d"] += record["recent_updates_30d"]
            overdue_buckets.update(counter_rows["overdue_buckets"])

            space_record = space_records_by_id[record["space_id"]]
            space_record["open_tasks"] += record["open_tasks"]
            space_record["recent_updates_30d"] += record["recent_updates_30d"]
            space_record["stale_open_tasks"] += record["stale_open_tasks"]
            space_record["overdue_open_tasks"] += record["overdue_open_tasks"]
            space_record["ownerless_open_tasks"] += record["ownerless_open_tasks"]
            space_record["no_due_date_open_tasks"] += record["no_due_date_open_tasks"]
            space_record["custom_field_occurrences"] += record["custom_field_count"]
            field_id_set = set(space_record.get("_field_ids", []))
            field_id_set.update(record["field_ids"])
            space_record["_field_ids"] = sorted(field_id_set)

            if idx % 25 == 0 or idx == total_lists:
                print(f"Completed {idx}/{total_lists} lists...")

    duplicate_field_names = []
    for norm_name, field_ids in sorted(fields_by_norm_name.items()):
        if len(field_ids) <= 1:
            continue
        duplicate_field_names.append(
            {
                "normalized_name": norm_name,
                "display_names": sorted({fields_by_id[field_id]["name"] for field_id in field_ids}),
                "field_ids": sorted(field_ids),
                "lists_using_any": sum(len(fields_by_id[field_id]["lists"]) for field_id in field_ids),
            }
        )

    low_coverage_fields = []
    for field_id, info in sorted(fields_by_id.items(), key=lambda item: (len(item[1]["lists"]), item[1]["name"].lower())):
        if len(info["lists"]) <= 1:
            low_coverage_fields.append(
                {
                    "field_id": field_id,
                    "name": info["name"],
                    "type": info["type"],
                    "lists_using_field": len(info["lists"]),
                    "spaces": sorted(info["spaces"]),
                }
            )

    space_records = []
    for record in space_records_by_id.values():
        field_ids = record.pop("_field_ids", [])
        record["distinct_field_ids"] = len(field_ids)
        space_records.append(record)

    space_records_sorted = sorted(space_records, key=lambda item: (-item["open_tasks"], item["space_name"].lower()))
    list_records_sorted = sorted(list_records, key=lambda item: (-item["open_tasks"], item["space_name"].lower(), item["list_name"].lower()))

    summary = {
        "spaces_audited": len(space_records),
        "spaces_archived_in_scope": sum(1 for space in spaces if space.archived),
        "folders_total": sum(record["folders_count"] for record in space_records),
        "lists_total": len(list_records),
        "mixed_hierarchy_spaces": sum(1 for record in space_records if record["mixed_hierarchy"]),
        "workflow_status_names_total": len(workflow_status_counter),
        "workflow_status_definitions_total": sum(workflow_status_counter.values()),
        "task_status_names_in_use_total": len(task_status_counter),
        "custom_field_ids_total": len(fields_by_id),
        "duplicate_custom_field_name_groups": len(duplicate_field_names),
        "low_coverage_field_ids": len(low_coverage_fields),
        "open_tasks_total": totals["open_tasks"],
        "ownerless_open_tasks": totals["ownerless_open_tasks"],
        "ownerless_open_tasks_pct": _pct(totals["ownerless_open_tasks"], totals["open_tasks"]),
        "multi_assignee_open_tasks": totals["multi_assignee_open_tasks"],
        "no_due_date_open_tasks": totals["no_due_date_open_tasks"],
        "no_due_date_open_tasks_pct": _pct(totals["no_due_date_open_tasks"], totals["open_tasks"]),
        "overdue_open_tasks": totals["overdue_open_tasks"],
        "overdue_open_tasks_pct": _pct(totals["overdue_open_tasks"], totals["open_tasks"]),
        "stale_open_tasks_30d": totals["stale_open_tasks"],
        "stale_open_tasks_30d_pct": _pct(totals["stale_open_tasks"], totals["open_tasks"]),
        "recently_updated_open_tasks_30d": totals["recent_updates_30d"],
        "errors_count": len(errors),
    }

    findings = _derive_findings(summary, space_records_sorted, list_records_sorted, duplicate_field_names, task_status_counter)

    return {
        "generated_at_utc": now.isoformat(),
        "scope": {
            "space_ids": [space.id for space in spaces],
            "space_names": [space.name for space in spaces],
        },
        "summary": summary,
        "overdue_buckets": overdue_buckets,
        "task_statuses_in_use": dict(task_status_counter.most_common()),
        "task_status_buckets": dict(task_status_bucket_counter),
        "workflow_status_inventory": {
            "by_name": dict(workflow_status_counter.most_common()),
            "by_type": dict(workflow_status_type_counter),
        },
        "custom_fields": {
            "duplicate_name_groups": duplicate_field_names,
            "low_coverage_fields": low_coverage_fields[:100],
        },
        "spaces": space_records_sorted,
        "lists": list_records_sorted,
        "findings": findings,
        "errors": errors,
    }


def _audit_list(
    *,
    client: ClickUpAuditClient,
    scope: ListAuditScope,
    stale_cutoff: datetime,
    now: datetime,
    ) -> tuple[dict[str, Any], list[tuple[str, str, str, str, str]], dict[str, Any], list[str]]:
    list_payload = scope.list_payload
    space = scope.space
    folder_payload = scope.folder_payload
    list_id = str(list_payload.get("id") or "")
    list_name = str(list_payload.get("name") or list_id)
    folder_name = None
    if folder_payload is not None:
        folder_name = str(folder_payload.get("name") or "")

    errors: list[str] = []
    field_rows: list[tuple[str, str, str, str, str]] = []
    field_ids: set[str] = set()
    effective_workflow_statuses = set(scope.effective_workflow_statuses)

    try:
        fields = client.list_list_fields(list_id)
    except Exception as exc:  # pragma: no cover - network failure path
        errors.append(f"list {space.name} / {list_name} ({list_id}) fields: {exc}")
        fields = []

    for field in fields:
        field_id = str(field.get("id") or "")
        name = str(field.get("name") or "").strip() or field_id
        field_type = str(field.get("type") or "").strip() or "unknown"
        if not field_id:
            continue
        field_ids.add(field_id)
        field_rows.append((field_id, name, field_type, list_id, space.name))

    try:
        tasks = client.list_open_tasks(list_id)
    except Exception as exc:  # pragma: no cover - network failure path
        errors.append(f"list {space.name} / {list_name} ({list_id}) open tasks: {exc}")
        tasks = []

    ownerless = 0
    multi_assignee = 0
    no_due_date = 0
    overdue = 0
    stale = 0
    updated_30d = 0
    task_statuses: list[str] = []
    overdue_counts = Counter({"1_7_days": 0, "8_30_days": 0, "31_90_days": 0, "91_plus_days": 0})

    for task in tasks:
        assignees = task.get("assignees")
        assignee_count = len(assignees) if isinstance(assignees, list) else 0
        if assignee_count == 0:
            ownerless += 1
        if assignee_count > 1:
            multi_assignee += 1

        due_date = _parse_clickup_millis(task.get("due_date"))
        if due_date is None:
            no_due_date += 1
        elif due_date < now:
            overdue += 1
            age_days = max(1, int((now - due_date).total_seconds() // 86400))
            if age_days <= 7:
                overdue_counts["1_7_days"] += 1
            elif age_days <= 30:
                overdue_counts["8_30_days"] += 1
            elif age_days <= 90:
                overdue_counts["31_90_days"] += 1
            else:
                overdue_counts["91_plus_days"] += 1

        updated_at = _parse_clickup_millis(task.get("date_updated"))
        if updated_at and updated_at >= stale_cutoff:
            updated_30d += 1
        else:
            stale += 1

        status_obj = task.get("status") if isinstance(task.get("status"), dict) else {}
        status_name = str(status_obj.get("status") or "").strip() or "unknown"
        task_statuses.append(status_name)

    open_tasks = len(tasks)
    record = {
        "space_id": space.id,
        "space_name": space.name,
        "folder_name": folder_name,
        "list_id": list_id,
        "list_name": list_name,
        "open_tasks": open_tasks,
        "ownerless_open_tasks": ownerless,
        "multi_assignee_open_tasks": multi_assignee,
        "no_due_date_open_tasks": no_due_date,
        "overdue_open_tasks": overdue,
        "stale_open_tasks": stale,
        "recent_updates_30d": updated_30d,
        "custom_field_count": len(fields),
        "field_ids": sorted(field_ids),
        "effective_workflow_statuses": sorted(effective_workflow_statuses),
        "hierarchy_type": "foldered" if folder_payload else "folderless",
        "ownerless_open_tasks_pct": _pct(ownerless, open_tasks),
        "no_due_date_open_tasks_pct": _pct(no_due_date, open_tasks),
        "overdue_open_tasks_pct": _pct(overdue, open_tasks),
        "stale_open_tasks_pct": _pct(stale, open_tasks),
        "active_list_candidate": updated_30d > 0,
        "abandoned_candidate": open_tasks == 0 or (updated_30d == 0 and open_tasks > 0),
    }
    return record, field_rows, {"task_statuses": task_statuses, "overdue_buckets": overdue_counts}, errors


def _derive_findings(
    summary: dict[str, Any],
    spaces: list[dict[str, Any]],
    lists: list[dict[str, Any]],
    duplicate_field_names: list[dict[str, Any]],
    task_status_counter: Counter[str],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    if summary["spaces_audited"] > HEALTHY_SPACE_TARGET_MAX:
        findings.append(
            {
                "severity": "high",
                "title": "Workspace sprawl",
                "detail": (
                    f"{summary['spaces_audited']} spaces were audited, which is materially above the "
                    f"target operating range of 6-10 for a company workspace."
                ),
            }
        )

    if summary["mixed_hierarchy_spaces"] > 0:
        findings.append(
            {
                "severity": "medium",
                "title": "Mixed folder/list hierarchy",
                "detail": (
                    f"{summary['mixed_hierarchy_spaces']} spaces mix folderless lists and folder-based lists, "
                    "which is a common source of inconsistent reporting and naming."
                ),
            }
        )

    if summary["ownerless_open_tasks_pct"] > 0:
        findings.append(
            {
                "severity": "high",
                "title": "Ownerless open tasks",
                "detail": (
                    f"{summary['ownerless_open_tasks']} open tasks ({summary['ownerless_open_tasks_pct']:.1f}%) "
                    "have no assignee."
                ),
            }
        )

    if summary["no_due_date_open_tasks_pct"] >= 10:
        findings.append(
            {
                "severity": "high",
                "title": "Missing due dates",
                "detail": (
                    f"{summary['no_due_date_open_tasks']} open tasks ({summary['no_due_date_open_tasks_pct']:.1f}%) "
                    "have no due date."
                ),
            }
        )

    if summary["overdue_open_tasks_pct"] >= 15:
        findings.append(
            {
                "severity": "high",
                "title": "Overdue backlog",
                "detail": (
                    f"{summary['overdue_open_tasks']} open tasks ({summary['overdue_open_tasks_pct']:.1f}%) "
                    "are overdue."
                ),
            }
        )

    if summary["stale_open_tasks_30d_pct"] >= 20:
        findings.append(
            {
                "severity": "medium",
                "title": "Stale work",
                "detail": (
                    f"{summary['stale_open_tasks_30d']} open tasks ({summary['stale_open_tasks_30d_pct']:.1f}%) "
                    "have not been updated in 30+ days."
                ),
            }
        )

    if duplicate_field_names:
        findings.append(
            {
                "severity": "medium",
                "title": "Duplicate custom-field names",
                "detail": (
                    f"{len(duplicate_field_names)} custom-field name group(s) map to more than one field ID, "
                    "which fragments reporting and automation references."
                ),
            }
        )

    if len(task_status_counter) > 12:
        findings.append(
            {
                "severity": "medium",
                "title": "Status sprawl in active tasks",
                "detail": (
                    f"Open tasks currently use {len(task_status_counter)} distinct status names."
                ),
            }
        )

    most_active_space = spaces[0] if spaces else None
    if most_active_space:
        findings.append(
            {
                "severity": "info",
                "title": "Primary activity center",
                "detail": (
                    f"The most active space by open tasks is {most_active_space['space_name']} "
                    f"with {most_active_space['open_tasks']} open tasks."
                ),
            }
        )

    abandoned_lists = [item for item in lists if item["abandoned_candidate"]]
    if abandoned_lists:
        findings.append(
            {
                "severity": "info",
                "title": "Inactive or abandoned lists",
                "detail": (
                    f"{len(abandoned_lists)} lists have no open work or no open-task updates in the last 30 days."
                ),
            }
        )

    return findings


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    spaces = report["spaces"][:10]
    lists = report["lists"][:15]
    duplicate_fields = report["custom_fields"]["duplicate_name_groups"][:15]
    findings = report["findings"]
    overdue = report["overdue_buckets"]

    lines = [
        "# ClickUp Workspace Audit",
        "",
        f"Generated at: `{report['generated_at_utc']}`",
        "",
        "## Headline Metrics",
        "",
        f"- Spaces audited: **{summary['spaces_audited']}**",
        f"- Folders: **{summary['folders_total']}**",
        f"- Lists: **{summary['lists_total']}**",
        f"- Open tasks: **{summary['open_tasks_total']}**",
        f"- Ownerless open tasks: **{summary['ownerless_open_tasks']}** ({summary['ownerless_open_tasks_pct']:.1f}%)",
        f"- Open tasks without due dates: **{summary['no_due_date_open_tasks']}** ({summary['no_due_date_open_tasks_pct']:.1f}%)",
        f"- Overdue open tasks: **{summary['overdue_open_tasks']}** ({summary['overdue_open_tasks_pct']:.1f}%)",
        f"- Stale open tasks (30+ days): **{summary['stale_open_tasks_30d']}** ({summary['stale_open_tasks_30d_pct']:.1f}%)",
        f"- Workflow status names defined: **{summary['workflow_status_names_total']}**",
        f"- Task status names currently in use: **{summary['task_status_names_in_use_total']}**",
        f"- Distinct custom field IDs: **{summary['custom_field_ids_total']}**",
        f"- Duplicate custom-field name groups: **{summary['duplicate_custom_field_name_groups']}**",
        "",
        "## Findings",
        "",
    ]

    if findings:
        for finding in findings:
            lines.append(f"- **[{finding['severity'].upper()}]** {finding['title']}: {finding['detail']}")
    else:
        lines.append("- No material findings were derived from the available API data.")

    lines.extend(
        [
            "",
            "## Overdue Backlog",
            "",
            f"- 1-7 days overdue: **{overdue['1_7_days']}**",
            f"- 8-30 days overdue: **{overdue['8_30_days']}**",
            f"- 31-90 days overdue: **{overdue['31_90_days']}**",
            f"- 91+ days overdue: **{overdue['91_plus_days']}**",
            "",
            "## Top Active Spaces",
            "",
        ]
    )

    for item in spaces:
        lines.append(
            f"- **{item['space_name']}**: {item['open_tasks']} open tasks, {item['lists_total']} lists, "
            f"{item['overdue_open_tasks']} overdue, {item['stale_open_tasks']} stale"
        )

    lines.extend(["", "## Top Active Lists", ""])
    for item in lists:
        folder_label = item["folder_name"] if item["folder_name"] else "No folder"
        lines.append(
            f"- **{item['space_name']} / {folder_label} / {item['list_name']}**: {item['open_tasks']} open, "
            f"{item['overdue_open_tasks']} overdue, {item['stale_open_tasks']} stale, "
            f"{item['ownerless_open_tasks']} ownerless"
        )

    lines.extend(["", "## Duplicate Custom-Field Names", ""])
    if duplicate_fields:
        for item in duplicate_fields:
            names = ", ".join(item["display_names"])
            lines.append(
                f"- **{names}**: {len(item['field_ids'])} field IDs across {item['lists_using_any']} list occurrences"
            )
    else:
        lines.append("- No duplicate custom-field names were detected.")

    lines.extend(
        [
            "",
            "## Limits",
            "",
            "- This report is read-only and based on ClickUp API data available to the configured token.",
            "- Native ClickUp dashboard definitions, automation rules, and true bypass behavior are not exposed in the same API shape, so they require UI review or exports for a complete audit.",
        ]
    )

    if report["errors"]:
        lines.extend(["", "## API Errors", ""])
        for error in report["errors"][:25]:
            lines.append(f"- {error}")

    lines.append("")
    return "\n".join(lines)


def _parse_clickup_millis(value: Any) -> datetime | None:
    if value in (None, "", "null"):
        return None
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)
    except Exception:
        return None


def _normalize(value: str) -> str:
    chars = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != " ":
            chars.append(" ")
    return "".join(chars).strip()


def _pct(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((value / total) * 100, 1)


def _status_bucket(status_name: str) -> str:
    norm = _normalize(status_name)
    if any(token in norm for token in ("done", "complete", "closed", "won", "delivered")):
        return "done"
    if any(token in norm for token in ("cancel", "lost")):
        return "cancelled"
    if any(token in norm for token in ("review", "pending", "wait", "hold", "blocked", "approval")):
        return "waiting"
    if any(token in norm for token in ("progress", "active", "working", "processing", "doing", "transit")):
        return "active"
    return "backlog_or_other"


if __name__ == "__main__":
    main()
