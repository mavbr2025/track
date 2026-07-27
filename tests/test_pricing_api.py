from __future__ import annotations

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient

from shipment_sync.api import create_app, _get_pricing_client, _get_pricing_settings
from shipment_sync.pricing_sync_config import PricingSyncSettings


class FakePricingClient:
    def __init__(self, shipment_task: dict | None = None, quote_task: dict | None = None) -> None:
        self.shipment_task = shipment_task
        self.quote_task = quote_task
        self.updated_fields: list[tuple[str, str, object]] = []

    def get_task(self, task_ref: str) -> dict:
        if "shipment" in task_ref:
            return dict(self.shipment_task or {})
        return dict(self.quote_task or {})

    def list_tasks(self, list_ids: list[str]) -> list[dict]:
        if "ship-list" in list_ids:
            return [dict(self.shipment_task or {})] if self.shipment_task else []
        if "quote-list" in list_ids:
            return [dict(self.quote_task or {})] if self.quote_task else []
        return []

    def update_custom_field(self, task_id: str, field_id: str, value: object) -> None:
        self.updated_fields.append((task_id, field_id, value))


def _settings() -> PricingSyncSettings:
    return PricingSyncSettings(
        clickup_api_token="token",
        clickup_oauth_access_token=None,
        clickup_team_id="8451352",
        clickup_shipment_list_id="ship-list",
        clickup_shipment_list_ids=["ship-list"],
        clickup_pricing_list_id="quote-list",
        clickup_pricing_list_ids=["quote-list"],
        clickup_pricing_match_field="MTM Quote #",
        clickup_pricing_shipment_match_fields=["MTM Quote #", "MTM Booking", "Booking number/", "Master BL Number/"],
        clickup_pricing_quote_match_fields=["MTM Quote #", "Shipment associated"],
        clickup_pricing_copy_fields=None,
        clickup_pricing_only_empty_targets=True,
        clickup_pricing_set_quote_number=True,
    )


@pytest.fixture
def trigger_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("SHIPMENT_API_TRIGGER_TOKEN", "test-trigger-token")
    return {"X-Trigger-Token": "test-trigger-token"}


def test_pricing_health_reports_missing_credentials(monkeypatch) -> None:
    app = create_app()
    client = TestClient(app)

    @classmethod
    def _raise_missing(cls) -> PricingSyncSettings:
        raise ValueError("Missing ClickUp credentials. Set CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN.")

    monkeypatch.setattr(PricingSyncSettings, "from_env", _raise_missing)

    response = client.get("/pricing/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "configured": False,
        "detail": "Missing ClickUp credentials. Set CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN.",
    }


def test_pricing_sync_pair_dry_run_returns_updates(trigger_headers: dict[str, str]) -> None:
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-24095",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
            {"id": "freight-field", "name": "Freight (Ocean/Truck/Air)", "value": ""},
            {"id": "containers", "name": "Number of Containers", "value": "2"},
        ],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3404",
        "name": "Quote Task",
        "custom_fields": [
            {"id": "freight-field", "name": "Freight (Ocean/Truck/Air)", "type": "currency", "value": "2500"},
        ],
    }
    fake_client = FakePricingClient(shipment_task=shipment_task, quote_task=quote_task)

    app = create_app()
    app.dependency_overrides[_get_pricing_settings] = _settings
    app.dependency_overrides[_get_pricing_client] = lambda: fake_client
    client = TestClient(app)

    response = client.post(
        "/pricing/sync",
        headers=trigger_headers,
        json={
            "shipment": "shipment-url",
            "quote": "quote-url",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "pair"
    assert payload["shipments_matched"] == 1
    assert payload["shipments_updated"] == 1
    assert payload["shipments_skipped"] == 0
    assert payload["results"][0]["applied_updates"] == 2
    assert payload["results"][0]["updates"][0]["value"] == "5000"
    assert payload["results"][0]["updates"][0]["transform"] == (
        "Freight (Ocean/Truck/Air) per container 2500 x 2 containers"
    )
    assert payload["results"][0]["updates"][1]["value"] == "MTMQUOTE-3404"
    assert fake_client.updated_fields == []


def test_pricing_sync_can_discover_quote_from_shipment_relationship(
    trigger_headers: dict[str, str],
) -> None:
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
            {"id": "mtm-booking", "name": "MTM Booking", "value": "MTMLXGT-25717"},
            {"id": "freight-field", "name": "Freight (Ocean/Truck/Air)", "value": ""},
            {"id": "containers", "name": "Number of Containers", "value": "1"},
        ],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3432",
        "name": "Quote Task",
        "custom_fields": [
            {
                "id": "shipment-associated",
                "name": "Shipment associated",
                "type": "tasks",
                "value": [
                    {
                        "id": "shipment-task-1",
                        "custom_id": "MTMLXGT-25717",
                        "name": "Shipment Task",
                        "url": "https://app.clickup.com/t/shipment-task-1",
                    }
                ],
            },
            {"id": "freight-field", "name": "Freight (Ocean/Truck/Air)", "type": "currency", "value": "2500"},
        ],
    }
    fake_client = FakePricingClient(shipment_task=shipment_task, quote_task=quote_task)

    app = create_app()
    app.dependency_overrides[_get_pricing_settings] = _settings
    app.dependency_overrides[_get_pricing_client] = lambda: fake_client
    client = TestClient(app)

    response = client.post(
        "/pricing/sync",
        headers=trigger_headers,
        json={
            "shipment": "shipment-url",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["quote_custom_id"] == "MTMQUOTE-3432"
    assert payload["results"][0]["match_selector"] == "shipment_custom_id"
    assert payload["results"][0]["match_value"] == "MTMLXGT-25717"


def test_pricing_sync_bulk_reports_skipped_shipments(trigger_headers: dict[str, str]) -> None:
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-24095",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": "MTMQUOTE-9999"},
        ],
    }
    fake_client = FakePricingClient(shipment_task=shipment_task, quote_task=None)

    app = create_app()
    app.dependency_overrides[_get_pricing_settings] = _settings
    app.dependency_overrides[_get_pricing_client] = lambda: fake_client
    client = TestClient(app)

    response = client.post(
        "/pricing/sync",
        headers=trigger_headers,
        json={
            "sync_linked_shipments": True,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "bulk"
    assert payload["shipments_matched"] == 0
    assert payload["shipments_updated"] == 0
    assert payload["shipments_skipped"] == 1
    assert payload["results"][0]["skip_reason"] == "no quote found for MTMQUOTE-9999"
