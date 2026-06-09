from __future__ import annotations

from shipment_sync.audit import SyncAuditStore
from shipment_sync.config import Settings
from shipment_sync.models import ShipmentRef


def _settings(db_path: str) -> Settings:
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
        cf_status_last_checked=None,
        cf_track_trace_snapshot=None,
        cf_eta=None,
        cf_etd=None,
        cf_discharge_date=None,
        cf_gate_in_full=None,
        cf_gate_out_empty=None,
        cf_gate_out_delivery=None,
        cf_gate_in_empty=None,
        shipment_allowed_lines=["maersk"],
        shipment_audit_db_path=db_path,
        shipment_audit_source="test-cron",
    )


def test_sync_audit_store_records_run_and_task_events(tmp_path) -> None:
    db_path = str(tmp_path / "audit.sqlite3")
    settings = _settings(db_path)
    store = SyncAuditStore(db_path)
    run_id = store.start_run(settings=settings)
    shipment = ShipmentRef(
        task_id="task-1",
        task_name="Shipment",
        shipping_line="maersk",
        booking_no="BOOK-1",
        container_no="CONT-1",
        list_id="list-1",
        list_name="Shipments",
    )

    store.log_event(run_id, level="info", message="Started")
    store.log_task(run_id, shipment=shipment, outcome="updated", message="ETA update")
    store.finish_run(
        run_id,
        status="success",
        total_candidates=1,
        updated=1,
        unchanged=0,
        skipped=0,
        candidates_by_list={"Shipments (list-1)": 1},
        updated_by_list={"Shipments (list-1)": 1},
        unchanged_by_list={},
    )

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].id == run_id
    assert runs[0].source == "test-cron"
    assert runs[0].status == "success"
    assert runs[0].allowed_lines == ["maersk"]
    assert runs[0].updated == 1

    detail = store.get_run(run_id)
    assert detail is not None
    assert detail["total_candidates"] == 1

    task_events = store.list_task_events(run_id=run_id)
    assert len(task_events) == 1
    assert task_events[0]["task_id"] == "task-1"
    assert task_events[0]["outcome"] == "updated"

    log_entries = store.list_log_entries(run_id=run_id)
    assert len(log_entries) == 1
    assert log_entries[0]["message"] == "Started"
