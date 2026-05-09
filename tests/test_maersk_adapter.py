from __future__ import annotations

import requests

from shipment_sync.carriers import maersk
from shipment_sync.carriers.maersk import MaerskAdapter
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
