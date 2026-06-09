from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import socket
import sqlite3
import sys
from typing import Any
from uuid import uuid4

from .config import Settings
from .models import ShipmentRef, ShipmentStatus


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AuditRun:
    id: str
    source: str | None
    started_at: str
    finished_at: str | None
    status: str
    host: str | None
    allowed_lines: list[str]
    total_candidates: int | None
    updated: int | None
    unchanged: int | None
    skipped: int | None
    error: str | None


@dataclass
class AuditTaskEvent:
    id: int
    run_id: str
    ts: str
    task_id: str | None
    task_name: str | None
    shipping_line: str | None
    list_id: str | None
    list_name: str | None
    outcome: str
    message: str | None
    error: str | None


class SyncAuditStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def start_run(self, *, settings: Settings, source: str | None = None) -> str:
        run_id = uuid4().hex
        now = utc_now_iso()
        self._execute(
            """
            INSERT INTO sync_runs (
                id, source, started_at, status, host, pid, allowed_lines, excluded_lines
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source or settings.shipment_audit_source,
                now,
                "running",
                socket.gethostname(),
                os.getpid(),
                _json(settings.shipment_allowed_lines or []),
                _json(settings.shipment_excluded_lines or []),
            ),
        )
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        total_candidates: int | None = None,
        updated: int | None = None,
        unchanged: int | None = None,
        skipped: int | None = None,
        candidates_by_list: dict[str, int] | None = None,
        updated_by_list: dict[str, int] | None = None,
        unchanged_by_list: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        self._execute(
            """
            UPDATE sync_runs
            SET finished_at = ?,
                status = ?,
                total_candidates = COALESCE(?, total_candidates),
                updated = COALESCE(?, updated),
                unchanged = COALESCE(?, unchanged),
                skipped = COALESCE(?, skipped),
                candidates_by_list = ?,
                updated_by_list = ?,
                unchanged_by_list = ?,
                error = ?
            WHERE id = ?
            """,
            (
                utc_now_iso(),
                status,
                total_candidates,
                updated,
                unchanged,
                skipped,
                _json(candidates_by_list or {}),
                _json(updated_by_list or {}),
                _json(unchanged_by_list or {}),
                error,
                run_id,
            ),
        )

    def log_event(
        self,
        run_id: str,
        *,
        level: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._execute(
            """
            INSERT INTO sync_log_entries (run_id, ts, level, message, data_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, utc_now_iso(), level, message, _json(data or {})),
        )

    def log_task(
        self,
        run_id: str,
        *,
        shipment: ShipmentRef,
        outcome: str,
        message: str | None = None,
        status: ShipmentStatus | None = None,
        error: str | None = None,
    ) -> None:
        latest_move = status.latest_move if status is not None else None
        self._execute(
            """
            INSERT INTO sync_task_events (
                run_id, ts, task_id, task_name, shipping_line, list_id, list_name,
                booking_no, container_no, outcome, message, status_text, location,
                event_time, eta_time, eta_local_text, latest_move_name,
                latest_move_location, latest_move_time_local_text, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                utc_now_iso(),
                shipment.task_id,
                shipment.task_name,
                shipment.shipping_line,
                shipment.list_id,
                shipment.list_name,
                shipment.booking_no,
                shipment.container_no,
                outcome,
                message,
                status.status_text if status is not None else None,
                status.location if status is not None else None,
                _dt(status.event_time if status is not None else None),
                _dt(status.eta_time if status is not None else None),
                status.eta_local_text if status is not None else None,
                latest_move.name if latest_move is not None else None,
                latest_move.location if latest_move is not None else None,
                latest_move.event_time_local_text if latest_move is not None else None,
                error,
            ),
        )

    def list_runs(self, *, limit: int = 50) -> list[AuditRun]:
        rows = self._query(
            """
            SELECT id, source, started_at, finished_at, status, host, allowed_lines,
                   total_candidates, updated, unchanged, skipped, error
            FROM sync_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        )
        return [_row_to_run(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM sync_runs WHERE id = ?", (run_id,))
        if not rows:
            return None
        return dict(rows[0])

    def list_task_events(self, *, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT *
            FROM sync_task_events
            WHERE run_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (run_id, max(1, min(limit, 2000))),
        )
        return [dict(row) for row in rows]

    def list_log_entries(self, *, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT *
            FROM sync_log_entries
            WHERE run_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (run_id, max(1, min(limit, 2000))),
        )
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        with self._connect() as conn:
            conn.execute(sql, params)

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(sql, params))

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    host TEXT,
                    pid INTEGER,
                    allowed_lines TEXT NOT NULL DEFAULT '[]',
                    excluded_lines TEXT NOT NULL DEFAULT '[]',
                    total_candidates INTEGER,
                    updated INTEGER,
                    unchanged INTEGER,
                    skipped INTEGER,
                    candidates_by_list TEXT NOT NULL DEFAULT '{}',
                    updated_by_list TEXT NOT NULL DEFAULT '{}',
                    unchanged_by_list TEXT NOT NULL DEFAULT '{}',
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS sync_task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    task_id TEXT,
                    task_name TEXT,
                    shipping_line TEXT,
                    list_id TEXT,
                    list_name TEXT,
                    booking_no TEXT,
                    container_no TEXT,
                    outcome TEXT NOT NULL,
                    message TEXT,
                    status_text TEXT,
                    location TEXT,
                    event_time TEXT,
                    eta_time TEXT,
                    eta_local_text TEXT,
                    latest_move_name TEXT,
                    latest_move_location TEXT,
                    latest_move_time_local_text TEXT,
                    error TEXT,
                    FOREIGN KEY(run_id) REFERENCES sync_runs(id)
                );

                CREATE TABLE IF NOT EXISTS sync_log_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(run_id) REFERENCES sync_runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sync_runs_started_at
                    ON sync_runs(started_at);
                CREATE INDEX IF NOT EXISTS idx_sync_task_events_run_id
                    ON sync_task_events(run_id);
                CREATE INDEX IF NOT EXISTS idx_sync_task_events_task_id
                    ON sync_task_events(task_id);
                CREATE INDEX IF NOT EXISTS idx_sync_task_events_outcome
                    ON sync_task_events(outcome);
                CREATE INDEX IF NOT EXISTS idx_sync_log_entries_run_id
                    ON sync_log_entries(run_id);
                """
            )


class SafeSyncAuditLogger:
    def __init__(self, store: SyncAuditStore, run_id: str):
        self.store = store
        self.run_id = run_id

    @classmethod
    def from_settings(cls, settings: Settings) -> "SafeSyncAuditLogger | None":
        if not settings.shipment_audit_db_path:
            return None
        try:
            store = SyncAuditStore(settings.shipment_audit_db_path)
            run_id = store.start_run(settings=settings)
            return cls(store=store, run_id=run_id)
        except Exception as exc:
            print(f"Shipment audit logging disabled: {exc}", file=sys.stderr)
            return None

    def log_event(self, *, level: str, message: str, data: dict[str, Any] | None = None) -> None:
        self._safe(lambda: self.store.log_event(self.run_id, level=level, message=message, data=data))

    def log_task(
        self,
        *,
        shipment: ShipmentRef,
        outcome: str,
        message: str | None = None,
        status: ShipmentStatus | None = None,
        error: str | None = None,
    ) -> None:
        self._safe(
            lambda: self.store.log_task(
                self.run_id,
                shipment=shipment,
                outcome=outcome,
                message=message,
                status=status,
                error=error,
            )
        )

    def finish_success(self, stats: Any) -> None:
        self._safe(
            lambda: self.store.finish_run(
                self.run_id,
                status="success",
                total_candidates=stats.total_candidates,
                updated=len(stats.updated_items),
                unchanged=stats.unchanged,
                skipped=stats.skipped,
                candidates_by_list=stats.candidates_by_list,
                updated_by_list=stats.updated_by_list,
                unchanged_by_list=stats.unchanged_by_list,
            )
        )

    def finish_failed(self, exc: Exception) -> None:
        self._safe(lambda: self.store.finish_run(self.run_id, status="failed", error=str(exc)))

    def _safe(self, callback: Any) -> None:
        try:
            callback()
        except Exception as exc:
            print(f"Shipment audit write failed: {exc}", file=sys.stderr)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _row_to_run(row: sqlite3.Row) -> AuditRun:
    return AuditRun(
        id=str(row["id"]),
        source=row["source"],
        started_at=str(row["started_at"]),
        finished_at=row["finished_at"],
        status=str(row["status"]),
        host=row["host"],
        allowed_lines=_loads_list(row["allowed_lines"]),
        total_candidates=row["total_candidates"],
        updated=row["updated"],
        unchanged=row["unchanged"],
        skipped=row["skipped"],
        error=row["error"],
    )


def _loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []
