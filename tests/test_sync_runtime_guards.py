from __future__ import annotations

from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import DEFAULT_SHIPMENT_TERMINAL_STATUSES, Settings
from shipment_sync.models import ShipmentRef
from shipment_sync.sync import (
    _carrier_call_timeout_seconds,
    _carrier_access_denied,
    _carrier_process_isolated,
    _carrier_process_timeout_seconds,
    _env_prefix_for_line,
    _run_sync,
    _shipment_min_sync_interval_hours,
    _terminal_shipment_status,
    _wan_hai_manual_capture_needed,
)


def _settings(**overrides: object) -> Settings:
    base = dict(
        clickup_api_token="token",
        clickup_oauth_access_token=None,
        clickup_oauth_client_id=None,
        clickup_oauth_client_secret=None,
        clickup_oauth_redirect_uri=None,
        clickup_list_id="list-1",
        clickup_list_ids=["list-1"],
        clickup_team_id=None,
        clickup_space_ids=[],
        clickup_folder_ids=[],
        clickup_discover_from_spaces=False,
        clickup_discover_from_team=False,
        cf_container_no="container-field",
        cf_booking_no="booking-field",
        cf_shipping_line="carrier-field",
        cf_shipment_status=None,
        cf_status_last_checked="last-checked-field",
        cf_track_trace_snapshot=None,
        cf_eta="eta-field",
        cf_etd="etd-field",
        cf_discharge_date="disc-field",
        cf_gate_in_full="gtin-full-field",
        cf_gate_out_empty="gtot-empty-field",
        cf_gate_out_delivery="gtot-delivery-field",
        cf_gate_in_empty="gtin-empty-field",
    )
    base.update(overrides)
    return Settings(**base)


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


def test_terminal_shipment_status_matches_approved_names() -> None:
    approved_statuses = [
        "blocked",
        "cancelado",
        "booking canceled",
        "booking cancelled",
        "canceled",
        "cancelled",
        "vacío devuelto",
        "vacio devuelto",
        "empty returned",
        "embarque cerrado",
        "closed",
        "completo en wf pagado",
        "completo en wf (pagado)",
    ]

    for status in approved_statuses:
        assert _terminal_shipment_status(status, DEFAULT_SHIPMENT_TERMINAL_STATUSES) is not None

    assert _terminal_shipment_status("en almacén", DEFAULT_SHIPMENT_TERMINAL_STATUSES) is None
    assert _terminal_shipment_status("released to consignee", DEFAULT_SHIPMENT_TERMINAL_STATUSES) is None


def test_run_sync_prefilters_terminal_status_before_carrier_lookup() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-terminal",
        task_name="Terminal shipment",
        shipping_line="one",
        booking_no="BOOK-1",
        container_no=None,
        list_id="list-1",
        current_task_status="VACIO DEVUELTO",
    )

    stats = _run_sync(client, shipments=[shipment])

    assert stats.total_candidates == 0
    assert stats.updated_items == []
    assert stats.unchanged == 0
    assert stats.skipped == 0


def test_wan_hai_manual_capture_detector_matches_antibot_errors() -> None:
    assert _wan_hai_manual_capture_needed(
        "Wan Hai",
        "WanHaiAntiBotBlocked: Wan Hai tracking page blocked by anti-bot protection",
    )
    assert _wan_hai_manual_capture_needed("wan hai", "query form not available")
    assert not _wan_hai_manual_capture_needed("one", "blocked by anti-bot protection")
    assert not _wan_hai_manual_capture_needed("wan hai", "returned no results")


def test_carrier_access_denied_matches_msc_browser_denial() -> None:
    assert _carrier_access_denied("MSC", "MSC page access denied in browser session")
    assert _carrier_access_denied("MSC", "MSC endpoint blocked by anti-bot challenge")
    assert not _carrier_access_denied("MSC", "MSC returned no result for this booking")


def test_run_sync_requests_wan_hai_manual_capture_on_antibot(monkeypatch) -> None:
    class _BlockedWanHaiAdapter:
        def fetch_status(self, shipment: ShipmentRef):
            raise ValueError("WanHaiAntiBotBlocked: Wan Hai tracking page blocked by anti-bot protection")

    class _ManualCaptureClient(ClickUpClient):
        def __init__(self) -> None:
            super().__init__(_settings())
            self.manual_capture_requests: list[tuple[str, str | None]] = []

        def request_wan_hai_manual_capture(self, shipment: ShipmentRef, *, error: str | None = None) -> bool:
            self.manual_capture_requests.append((shipment.task_id, error))
            return True

    monkeypatch.setattr(
        "shipment_sync.sync.build_carrier_registry",
        lambda: {"wan hai": _BlockedWanHaiAdapter()},
    )
    client = _ManualCaptureClient()
    shipment = ShipmentRef(
        task_id="task-wan-hai",
        task_name="Wan Hai shipment",
        shipping_line="wan hai",
        booking_no="027G709927",
        container_no=None,
        list_id="list-1",
    )

    stats = _run_sync(client, shipments=[shipment])

    assert stats.total_candidates == 1
    assert stats.skipped == 1
    assert client.manual_capture_requests == [
        ("task-wan-hai", "WanHaiAntiBotBlocked: Wan Hai tracking page blocked by anti-bot protection")
    ]
