from __future__ import annotations

from datetime import datetime, timezone

from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import Settings
from shipment_sync.date_utils import format_port_local_time
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus


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


def test_plan_shipment_update_maps_origin_and_destination_events_to_fields() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-1",
        task_name="Shipment 1",
        shipping_line="maersk",
        booking_no="BOOK-1",
        container_no="CONT-1",
        list_id="list-1",
    )
    status = ShipmentStatus(
        status_text="In transit",
        eta_time=datetime(2026, 3, 25, 8, 0, tzinfo=timezone.utc),
        eta_local_text="2026-03-25 14:00",
        recent_moves=[
            MovementEvent(
                name="Container Gated In (GTIN)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 3, 23, 18, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Container Gated Out (GTOT)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Container Discharged (DISC)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Transport Departed (DEPA)",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 10, 18, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Container Gated In (GTIN)",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 6, 10, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Empty Container Release to Shipper",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 4, 8, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    plan = client.plan_shipment_update(shipment, status)

    updates = {update.label: update for update in plan.custom_field_updates}
    assert updates["ETA"].field_id == "eta-field"
    assert updates["ETA"].value.date().isoformat() == "2026-03-25"
    assert updates["ETD"].field_id == "etd-field"
    assert updates["ETD"].value.date().isoformat() == "2026-02-10"
    assert updates["Discharge date"].field_id == "disc-field"
    assert updates["Discharge date"].value.date().isoformat() == "2026-03-22"
    assert updates["Gate-in full"].field_id == "gtin-full-field"
    assert updates["Gate-in full"].value.date().isoformat() == "2026-02-06"
    assert updates["Gate out empty"].field_id == "gtot-empty-field"
    assert updates["Gate out empty"].value.date().isoformat() == "2026-02-04"
    assert updates["Gate out delivery"].field_id == "gtot-delivery-field"
    assert updates["Gate out delivery"].value.date().isoformat() == "2026-03-23"
    assert updates["Gate in empty"].field_id == "gtin-empty-field"
    assert updates["Gate in empty"].value.date().isoformat() == "2026-03-23"
    assert plan.comment_text is not None
    assert "Last checked (UTC): " in plan.comment_text
    assert "ETA (port local time): 2026-03-25 14:00" in plan.comment_text


def test_plan_shipment_update_skips_destination_events_until_discharge_exists() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-2",
        task_name="Shipment 2",
        shipping_line="maersk",
        booking_no="BOOK-2",
        container_no="CONT-2",
        list_id="list-1",
    )
    status = ShipmentStatus(
        status_text="Origin leg",
        recent_moves=[
            MovementEvent(
                name="Transport Departed (DEPA)",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 10, 18, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Container Gated In (GTIN)",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 6, 10, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Empty Container Release to Shipper",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 4, 8, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    plan = client.plan_shipment_update(shipment, status)

    updates = {update.label: update for update in plan.custom_field_updates}
    assert "Gate-in full" in updates
    assert "Gate out empty" in updates
    assert "ETD" in updates
    assert "Discharge date" not in updates
    assert "Gate out delivery" not in updates
    assert "Gate in empty" not in updates


def test_plan_shipment_update_only_refreshes_last_checked_when_all_values_match() -> None:
    client = ClickUpClient(_settings())
    def ms(year: int, month: int, day: int) -> str:
        return str(int(datetime(year, month, day, 12, 0, tzinfo=timezone.utc).timestamp() * 1000))

    shipment = ShipmentRef(
        task_id="task-3",
        task_name="Shipment 3",
        shipping_line="one",
        booking_no="BOOK-3",
        container_no="CONT-3",
        list_id="list-1",
        current_field_values={
            "eta-field": ms(2026, 5, 5),
            "etd-field": ms(2026, 2, 25),
            "disc-field": ms(2026, 5, 5),
            "gtin-full-field": ms(2026, 2, 13),
            "gtot-empty-field": ms(2026, 2, 13),
            "gtot-delivery-field": ms(2026, 5, 5),
            "gtin-empty-field": ms(2026, 5, 6),
        },
    )
    status = ShipmentStatus(
        status_text="ETA 2026-05-05",
        eta_time=datetime(2026, 5, 5, 8, 0, tzinfo=timezone.utc),
        eta_local_text="2026-05-05",
        recent_moves=[
            MovementEvent(
                name="Container Gated In (GTIN)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Container Gated Out (GTOT)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Container Discharged (DISC)",
                location="PUERTO QUETZAL",
                event_time=datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Transport Departed (DEPA)",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 25, 0, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Container Gated In (GTIN)",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 13, 0, 0, tzinfo=timezone.utc),
            ),
            MovementEvent(
                name="Empty Container Release to Shipper",
                location="NINGBO, ZHEJIANG",
                event_time=datetime(2026, 2, 13, 0, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    plan = client.plan_shipment_update(shipment, status)

    assert plan.changed is False
    assert [update.label for update in plan.custom_field_updates] == ["Last T&T Update"]
    assert plan.comment_text is not None
    assert "No change found" in plan.comment_text
    assert "T&T executed on " in plan.comment_text


def test_format_port_local_time_preserves_carrier_clock_time() -> None:
    assert format_port_local_time("2026-03-27T19:16:00.000Z", None) == "2026-03-27 19:16"
    assert format_port_local_time("27/03/2026 19:16", None) == "2026-03-27 19:16"
    assert format_port_local_time("2026-05-02", None) == "2026-05-02"
