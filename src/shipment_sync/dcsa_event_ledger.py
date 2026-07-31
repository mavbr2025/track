from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .dcsa_tnt import DcsaTntEvent


_PROJECTION_STATES = frozenset({"pending", "requires_review", "halted", "projected", "failed"})


@dataclass(frozen=True)
class DcsaLedgerWrite:
    event_key: str
    created: bool
    projection_state: str


class DcsaEventLedger:
    """SQLite-backed canonical event ledger for local and shadow-mode use.

    Production storage is deliberately a later adapter: this class establishes
    the event contract, idempotency behavior, and audit shape without changing
    the current scheduled workers.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def record(self, event: DcsaTntEvent, *, task_id: str | None = None, received_at: datetime | None = None) -> DcsaLedgerWrite:
        received = (received_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
        normalized_json = _json(event.normalized_record())
        raw_payload_json = _json(event.raw_payload)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO dcsa_events (
                    event_key, carrier, tnt_version, event_id, event_type, event_code,
                    event_classifier_code, event_created_at, event_at, task_id,
                    projection_state, received_at, normalized_json, raw_payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO NOTHING
                """,
                (
                    event.idempotency_key,
                    event.carrier,
                    event.tnt_version,
                    event.event_id,
                    event.event_type,
                    event.event_code,
                    event.event_classifier_code,
                    event.event_created_at.isoformat(),
                    event.event_at.isoformat() if event.event_at else None,
                    task_id,
                    event.initial_projection_state,
                    received,
                    normalized_json,
                    raw_payload_json,
                ),
            )
            if cursor.rowcount:
                return DcsaLedgerWrite(event_key=event.idempotency_key, created=True, projection_state=event.initial_projection_state)
            row = conn.execute(
                "SELECT projection_state FROM dcsa_events WHERE event_key = ?",
                (event.idempotency_key,),
            ).fetchone()
        if row is None:  # pragma: no cover - defensive against an unexpected database failure
            raise RuntimeError(f"DCSA event {event.idempotency_key} was not persisted.")
        return DcsaLedgerWrite(event_key=event.idempotency_key, created=False, projection_state=str(row["projection_state"]))

    def mark_projection(self, event_key: str, *, state: str, result: dict[str, Any] | None = None) -> None:
        if state not in _PROJECTION_STATES:
            raise ValueError(f"Unsupported projection state {state!r}.")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE dcsa_events
                SET projection_state = ?, projected_at = ?, projection_result_json = ?
                WHERE event_key = ?
                """,
                (
                    state,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    _json(result or {}),
                    event_key,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown DCSA event key {event_key!r}.")

    def get(self, event_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM dcsa_events WHERE event_key = ?", (event_key,)).fetchone()
        return _row_to_record(row) if row is not None else None

    def list_events(self, *, carrier: str | None = None, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if carrier:
            clauses.append("carrier = ?")
            params.append(carrier.strip().lower())
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM dcsa_events {where} ORDER BY received_at ASC, event_key ASC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dcsa_events (
                    event_key TEXT PRIMARY KEY,
                    carrier TEXT NOT NULL,
                    tnt_version TEXT NOT NULL,
                    event_id TEXT,
                    event_type TEXT NOT NULL,
                    event_code TEXT NOT NULL,
                    event_classifier_code TEXT,
                    event_created_at TEXT NOT NULL,
                    event_at TEXT,
                    task_id TEXT,
                    projection_state TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    projected_at TEXT,
                    normalized_json TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL,
                    projection_result_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_dcsa_events_carrier_received
                    ON dcsa_events(carrier, received_at);
                CREATE INDEX IF NOT EXISTS idx_dcsa_events_task_received
                    ON dcsa_events(task_id, received_at);
                CREATE INDEX IF NOT EXISTS idx_dcsa_events_projection_state
                    ON dcsa_events(projection_state, received_at);
                """
            )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    for key in ("normalized_json", "raw_payload_json", "projection_result_json"):
        record[key.removesuffix("_json")] = json.loads(record.pop(key))
    return record
