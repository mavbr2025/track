from __future__ import annotations

from datetime import datetime, timezone

from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import Settings
from shipment_sync.carriers.one import (
    OneAdapter,
    _extract_eta_from_cargo_events,
    _latest_move_from_search_item,
)
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _StubSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get(self, url: str, params: dict[str, str], timeout: int) -> _StubResponse:
        return _StubResponse(self.payload)


def _settings() -> Settings:
    return Settings(
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


def test_fetch_recent_moves_uses_trigger_type_for_actual_events() -> None:
    adapter = OneAdapter()
    adapter.session = _StubSession(
        {
            "data": [
                {
                    "eventName": "Unloaded from Vessel at Port of Discharging",
                    "eventLocalPortDate": "2026-04-09T17:26:00+00:00",
                    "triggerType": "ACTUAL",
                    "location": {"locationName": "PUERTO QUETZAL"},
                }
            ]
        }
    )

    moves = adapter._fetch_recent_moves("BOOK-1", "CONT-1")

    assert len(moves) == 1
    assert moves[0].name == "Container Discharged (DISC)"
    assert moves[0].location == "PUERTO QUETZAL"
    assert moves[0].event_time == datetime(2026, 4, 9, 17, 26, tzinfo=timezone.utc)
    assert moves[0].event_state == "actual"


def test_extract_eta_from_cargo_events_uses_trigger_type_for_estimated_events() -> None:
    eta_time, eta_raw = _extract_eta_from_cargo_events(
        [
            {
                "triggerType": "ESTIMATED",
                "locationName": "PUERTO QUETZAL",
                "localPortDate": "2026-05-20T04:00:00+00:00",
            }
        ],
        matrix_ids=set(),
        pod_name="PUERTO QUETZAL",
    )

    assert eta_time == datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc)
    assert eta_raw == "2026-05-20T04:00:00+00:00"


def test_latest_move_from_search_item_uses_trigger_type() -> None:
    move = _latest_move_from_search_item(
        {
            "latestEvent": {
                "eventName": "Vessel Arrival at Port of Discharge",
                "locationName": "PUERTO QUETZAL",
                "date": "2026-04-09T11:20:00+00:00",
                "triggerType": "ACTUAL",
            }
        }
    )

    assert move is not None
    assert move.name == "Transport Arrived (ARRI)"
    assert move.location == "PUERTO QUETZAL"
    assert move.event_time == datetime(2026, 4, 9, 11, 20, tzinfo=timezone.utc)
    assert move.event_state == "actual"


def test_plan_shipment_update_writes_final_destination_discharge_on_multi_leg_route() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-1",
        task_name="Shipment 1",
        shipping_line="one",
        booking_no="BOOK-1",
        container_no="CONT-1",
        list_id="list-1",
        current_field_values={"disc-field": None},
    )
    status = ShipmentStatus(
        status_text="ETA 2026-04-09",
        recent_moves=[
            MovementEvent(
                name="Container Gated In (GTIN)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 4, 10, 3, 56, tzinfo=timezone.utc),
                event_state="estimated",
            ),
            MovementEvent(
                name="Container Gated Out (GTOT)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 4, 9, 21, 56, tzinfo=timezone.utc),
                event_state="estimated",
            ),
            MovementEvent(
                name="Container Discharged (DISC)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 4, 9, 17, 26, tzinfo=timezone.utc),
                event_state="actual",
            ),
            MovementEvent(
                name="Transport Arrived (ARRI)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 4, 9, 11, 20, tzinfo=timezone.utc),
                event_state="actual",
            ),
            MovementEvent(
                name="Container Loaded (LOAD)",
                location="LAZARO CARDENAS",
                event_time=datetime(2026, 4, 5, 21, 31, tzinfo=timezone.utc),
                event_state="actual",
            ),
            MovementEvent(
                name="Container Discharged (DISC)",
                location="LAZARO CARDENAS",
                event_time=datetime(2026, 3, 30, 2, 8, tzinfo=timezone.utc),
                event_state="actual",
            ),
            MovementEvent(
                name="Transport Arrived (ARRI)",
                location="LAZARO CARDENAS",
                event_time=datetime(2026, 3, 29, 18, 25, tzinfo=timezone.utc),
                event_state="actual",
            ),
            MovementEvent(
                name="Transport Departed (DEPA)",
                location="PUSAN",
                event_time=datetime(2026, 3, 9, 17, 24, tzinfo=timezone.utc),
                event_state="actual",
            ),
            MovementEvent(
                name="Feeder Loading at O/B Inland Port",
                location="ZHAPU, ZHEJIANG",
                event_time=None,
                event_state="actual",
            ),
            MovementEvent(
                name="Transport Departed (DEPA)",
                location="LAZARO CARDENAS",
                event_time=None,
                event_state="actual",
            ),
        ],
    )

    plan = client.plan_shipment_update(shipment, status)
    updates = {update.label: update for update in plan.custom_field_updates}

    assert updates["Discharge date"].field_id == "disc-field"
    assert updates["Discharge date"].value.date().isoformat() == "2026-04-09"
