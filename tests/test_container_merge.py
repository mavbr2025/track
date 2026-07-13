from __future__ import annotations

from datetime import datetime, timezone

from shipment_sync.carriers.common import extract_container_numbers
from shipment_sync.clickup_client import ClickUpClient, _expected_container_count
from shipment_sync.config import Settings
from shipment_sync.models import ShipmentRef, ShipmentStatus


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


def test_extract_container_numbers_finds_unique_tokens_in_nested_payload() -> None:
    payload = {
        "bookingNo": "ABC123",
        "containers": [
            {"containerNo": "oneu2609800"},
            {"containerNo": "GCXU2286278"},
        ],
        "details": {
            "text": "Assigned: GCXU2286278, TEMU0651116",
        },
    }

    assert set(extract_container_numbers(payload)) == {
        "ONEU2609800",
        "GCXU2286278",
        "TEMU0651116",
    }


def test_expected_container_count_reads_clickup_number_field() -> None:
    assert _expected_container_count(
        [
            {"id": "other", "name": "Container Type", "value": "40HC"},
            {"id": "count", "name": "Number of Containers", "value": "1"},
        ]
    ) == 1


def test_plan_shipment_update_merges_discovered_containers_into_field() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-1",
        task_name="Shipment 1",
        shipping_line="one",
        booking_no="BOOK-1",
        container_no="GCXU2286278, MOAU5808200",
        list_id="list-1",
        current_field_values={"container-field": "GCXU2286278, MOAU5808200"},
    )
    status = ShipmentStatus(
        status_text="ETA 2026-05-06",
        eta_time=datetime(2026, 5, 6, 4, 0, tzinfo=timezone.utc),
        eta_local_text="2026-05-06T04:00:00.000Z",
        discovered_containers=["GCXU2286278", "ONEU2609800", "TEMU0651116"],
    )

    plan = client.plan_shipment_update(shipment, status)
    updates = {update.label: update for update in plan.custom_field_updates}

    assert updates["Container"].field_id == "container-field"
    assert updates["Container"].value == "GCXU2286278, MOAU5808200, ONEU2609800, TEMU0651116"


def test_plan_shipment_update_does_not_overwrite_with_subset() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-2",
        task_name="Shipment 2",
        shipping_line="one",
        booking_no="BOOK-2",
        container_no="GCXU2286278, MOAU5808200",
        list_id="list-1",
        current_field_values={"container-field": "GCXU2286278, MOAU5808200"},
    )
    status = ShipmentStatus(
        status_text="ETA 2026-05-06",
        eta_time=datetime(2026, 5, 6, 4, 0, tzinfo=timezone.utc),
        eta_local_text="2026-05-06T04:00:00.000Z",
        discovered_containers=["GCXU2286278"],
    )

    plan = client.plan_shipment_update(shipment, status)
    updates = {update.label: update for update in plan.custom_field_updates}

    assert "Container" not in updates


def test_plan_shipment_update_caps_discovered_containers_at_declared_count() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-3",
        task_name="Shipment 3",
        shipping_line="one",
        booking_no="BOOK-3",
        container_no="ONEU0005230",
        list_id="list-1",
        expected_container_count=1,
        current_field_values={"container-field": "ONEU0005230"},
    )
    status = ShipmentStatus(
        status_text="ETA 2026-05-06",
        discovered_containers=["ONEU0005230", "FFAU3663993"],
    )

    plan = client.plan_shipment_update(shipment, status)
    updates = {update.label: update for update in plan.custom_field_updates}

    assert "Container" not in updates


def test_plan_shipment_update_reconciles_overfilled_authoritative_container_result() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-4",
        task_name="Shipment 4",
        shipping_line="one",
        booking_no="SHAGT3664400",
        container_no="ONEU0005230, FFAU3663993",
        list_id="list-1",
        expected_container_count=1,
        current_field_values={"container-field": "ONEU0005230, FFAU3663993"},
    )
    status = ShipmentStatus(
        status_text="ETA 2026-05-06",
        discovered_containers=["ONEU0005230"],
        container_discovery_authoritative=True,
    )

    plan = client.plan_shipment_update(shipment, status)
    updates = {update.label: update for update in plan.custom_field_updates}

    assert updates["Container"].value == "ONEU0005230"


def test_plan_shipment_update_does_not_remove_overfilled_non_authoritative_result() -> None:
    client = ClickUpClient(_settings())
    shipment = ShipmentRef(
        task_id="task-5",
        task_name="Shipment 5",
        shipping_line="one",
        booking_no="BOOK-5",
        container_no="ONEU0005230, FFAU3663993",
        list_id="list-1",
        expected_container_count=1,
        current_field_values={"container-field": "ONEU0005230, FFAU3663993"},
    )
    status = ShipmentStatus(
        status_text="ETA 2026-05-06",
        discovered_containers=["ONEU0005230"],
    )

    plan = client.plan_shipment_update(shipment, status)
    updates = {update.label: update for update in plan.custom_field_updates}

    assert "Container" not in updates
