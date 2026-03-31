from __future__ import annotations

import threading

from shipment_sync import playwright_runner


def test_run_sync_playwright_runs_inline_without_event_loop(monkeypatch) -> None:
    monkeypatch.setattr(playwright_runner, "_has_running_event_loop", lambda: False)

    thread_name = playwright_runner.run_sync_playwright(lambda: threading.current_thread().name)

    assert thread_name == threading.current_thread().name


def test_run_sync_playwright_uses_worker_thread_when_event_loop_active(monkeypatch) -> None:
    monkeypatch.setattr(playwright_runner, "_has_running_event_loop", lambda: True)

    thread_name = playwright_runner.run_sync_playwright(lambda: threading.current_thread().name)

    assert thread_name != threading.current_thread().name
    assert thread_name.startswith("playwright-sync")
