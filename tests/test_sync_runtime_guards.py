from __future__ import annotations

from shipment_sync.sync import (
    _carrier_call_timeout_seconds,
    _carrier_process_isolated,
    _carrier_process_timeout_seconds,
    _env_prefix_for_line,
    _shipment_min_sync_interval_hours,
)


def test_carrier_call_timeout_prefers_line_specific_env(monkeypatch) -> None:
    monkeypatch.setenv("SHIPMENT_PER_TASK_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("MSC_PER_TASK_TIMEOUT_SECONDS", "75")

    assert _carrier_call_timeout_seconds("msc") == 75


def test_carrier_call_timeout_falls_back_to_global_env(monkeypatch) -> None:
    monkeypatch.setenv("SHIPMENT_PER_TASK_TIMEOUT_SECONDS", "20")

    assert _carrier_call_timeout_seconds("wan hai") == 20


def test_carrier_call_timeout_allows_line_specific_disable(monkeypatch) -> None:
    monkeypatch.setenv("SHIPMENT_PER_TASK_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("WAN_HAI_PER_TASK_TIMEOUT_SECONDS", "0")

    assert _carrier_call_timeout_seconds("wan hai") == 0


def test_env_prefix_for_shipping_line() -> None:
    assert _env_prefix_for_line("CMA - CGM") == "CMA_CGM"


def test_carrier_process_isolation_uses_normalized_line_names(monkeypatch) -> None:
    monkeypatch.setenv("SHIPMENT_PROCESS_ISOLATED_LINES", "wan hai,cma - cgm")

    assert _carrier_process_isolated("Wan Hai")
    assert _carrier_process_isolated("CMA - CGM")
    assert not _carrier_process_isolated("one")


def test_carrier_process_timeout_prefers_line_specific_env(monkeypatch) -> None:
    monkeypatch.setenv("SHIPMENT_PROCESS_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("WAN_HAI_PROCESS_TIMEOUT_SECONDS", "180")

    assert _carrier_process_timeout_seconds("wan hai") == 180
    assert _carrier_process_timeout_seconds("one") == 60


def test_min_sync_interval_prefers_line_specific_env(monkeypatch) -> None:
    monkeypatch.setenv("MSC_MIN_SYNC_INTERVAL_HOURS", "18")

    assert _shipment_min_sync_interval_hours("msc", 0) == 18
    assert _shipment_min_sync_interval_hours("one", 0) == 0
