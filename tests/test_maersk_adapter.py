from __future__ import annotations

import requests

from shipment_sync.carriers import maersk
from shipment_sync.carriers.maersk import MaerskAdapter, _status_from_events
from shipment_sync.models import ShipmentRef


def _shipment() -> ShipmentRef:
    return ShipmentRef(
        task_id="task-1",
        task_name="Maersk shipment",
        shipping_line="maersk",
        booking_no=None,
        container_no="CAAU8312730",
        list_id="list-1",
    )


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://api.maersk.com/track-and-trace-private/events"
    return requests.HTTPError(f"{status_code} error", response=response)


def test_maersk_events_404_returns_empty_status_without_web_fallback(monkeypatch) -> None:
    monkeypatch.setenv("MAERSK_API_MODE", "events")
    monkeypatch.setenv("MAERSK_TRACKING_API_URL", "https://api.maersk.com/track-and-trace-private/events")
    monkeypatch.setenv("MAERSK_BEARER_TOKEN", "token")
    monkeypatch.setenv("MAERSK_CONSUMER_KEY", "consumer")
    monkeypatch.setenv("MAERSK_WEB_FALLBACK_ON_API_ERROR", "true")

    def fake_get_with_retries(*args, **kwargs):
        assert kwargs["non_retry_statuses"] == {401, 403, 404}
        raise _http_error(404)

    def fail_web_fallback(*args, **kwargs):
        raise AssertionError("404 should not use Maersk web fallback")

    monkeypatch.setattr(maersk, "get_with_retries", fake_get_with_retries)
    adapter = MaerskAdapter()
    monkeypatch.setattr(adapter, "_try_web_fallback", fail_web_fallback)

    status = adapter.fetch_status(_shipment())

    assert status.status_text == "ETA unavailable"
    assert status.recent_moves == []
    assert status.raw_source == "maersk-events-api:not-found:https://api.maersk.com/track-and-trace-private/events"


def test_maersk_events_401_still_raises_without_web_fallback(monkeypatch) -> None:
    monkeypatch.setenv("MAERSK_API_MODE", "events")
    monkeypatch.setenv("MAERSK_TRACKING_API_URL", "https://api.maersk.com/track-and-trace-private/events")
    monkeypatch.setenv("MAERSK_BEARER_TOKEN", "token")
    monkeypatch.setenv("MAERSK_CONSUMER_KEY", "consumer")
    monkeypatch.setenv("MAERSK_WEB_FALLBACK_ON_API_ERROR", "true")

    def fake_get_with_retries(*args, **kwargs):
        raise _http_error(401)

    def fail_web_fallback(*args, **kwargs):
        raise AssertionError("401 should not use Maersk web fallback")

    monkeypatch.setattr(maersk, "get_with_retries", fake_get_with_retries)
    adapter = MaerskAdapter()
    monkeypatch.setattr(adapter, "_try_web_fallback", fail_web_fallback)

    try:
        adapter.fetch_status(_shipment())
    except requests.HTTPError as exc:
        assert exc.response is not None
        assert exc.response.status_code == 401
    else:
        raise AssertionError("Expected 401 HTTPError")


def test_maersk_tries_later_containers_when_first_has_no_events(monkeypatch) -> None:
    monkeypatch.setenv("MAERSK_API_MODE", "events")
    monkeypatch.setenv("MAERSK_TRACKING_API_URL", "https://api.maersk.com/track-and-trace-private/events")
    monkeypatch.setenv("MAERSK_BEARER_TOKEN", "token")
    monkeypatch.setenv("MAERSK_CONSUMER_KEY", "consumer")

    calls: list[tuple[str, str]] = []

    def fake_fetch_payload(self, reference: str, ref_type: str):
        calls.append((reference, ref_type))
        if reference == "MRKU0516710":
            return {"events": []}, "maersk-events-api:not-found"
        return {
            "events": [
                {
                    "eventType": "TRANSPORT",
                    "transportEventTypeCode": "DEPA",
                    "eventDateTime": "2026-05-01T10:00:00Z",
                    "locationName": "SHANGHAI",
                }
            ]
        }, f"maersk-events-api:{reference}"

    monkeypatch.setattr(MaerskAdapter, "_fetch_payload", fake_fetch_payload)

    status = MaerskAdapter().fetch_status(
        ShipmentRef(
            task_id="task-1",
            task_name="Maersk shipment",
            shipping_line="maersk",
            booking_no="269822607",
            container_no="MRKU0516710, MRKU0931970, MSKU6547177",
            list_id="list-1",
        )
    )

    assert calls == [("MRKU0516710", "container"), ("MRKU0931970", "container")]
    assert status.latest_move is not None
    assert status.latest_move.name == "Transport Departed (DEPA)"
    assert status.raw_source == "maersk-events-api:MRKU0931970"


def test_maersk_status_sets_final_arrival_vessel_voyage() -> None:
    status = _status_from_events(
        [
            {
                "transportEventTypeCode": "DEPA",
                "eventDateTime": "2026-05-01T10:00:00Z",
                "locationName": "NINGBO",
                "vesselName": "MAERSK ORIGIN",
                "carrierExportVoyageNumber": "601E",
            },
            {
                "transportEventTypeCode": "ARRI",
                "eventDateTime": "2026-06-04T07:00:00Z",
                "locationName": "PUERTO QUETZAL",
                "vesselName": "MAERSK FINAL",
                "carrierExportVoyageNumber": "621E",
            },
        ],
        "maersk-events-api:test",
        source_url="https://www.maersk.com/tracking/CONT",
    )

    assert status.vessel_voyage == "MAERSK FINAL 621E"
