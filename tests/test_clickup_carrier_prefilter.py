from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import Settings


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
        cf_status_last_checked=None,
        cf_track_trace_snapshot=None,
        cf_eta=None,
        cf_etd=None,
        cf_discharge_date=None,
        cf_gate_in_full=None,
        cf_gate_out_empty=None,
        cf_gate_out_delivery=None,
        cf_gate_in_empty=None,
    )
    base.update(overrides)
    return Settings(**base)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, error: Exception | None = None):
        self._payload = payload
        self._error = error

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self._error:
            raise self._error


class _FakeSession:
    def __init__(self, *, task_responses: list[_FakeResponse]):
        self.headers: dict[str, str] = {}
        self.task_responses = task_responses
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get(self, url: str, *, params: dict[str, str] | None = None, timeout: int) -> _FakeResponse:
        self.calls.append((url, params))
        if url.endswith("/list/list-1/field"):
            return _FakeResponse(
                {
                    "fields": [
                        {
                            "id": "carrier-field",
                            "name": "Carrier/",
                            "type": "drop_down",
                            "type_config": {
                                "options": [
                                    {
                                        "id": "one-option-id",
                                        "name": "ONE",
                                        "orderindex": 0,
                                    },
                                    {
                                        "id": "maersk-option-id",
                                        "name": "Maersk",
                                        "orderindex": 1,
                                    },
                                ]
                            },
                        }
                    ]
                }
            )
        if url.endswith("/list/list-1/task"):
            return self.task_responses.pop(0)
        raise AssertionError(f"unexpected URL: {url}")


class _DiscoverySession:
    def __init__(self, *, fail_on_get: bool = False):
        self.headers: dict[str, str] = {}
        self.fail_on_get = fail_on_get
        self.calls: list[str] = []

    def get(self, url: str, *, params: dict[str, str] | None = None, timeout: int) -> _FakeResponse:
        if self.fail_on_get:
            raise AssertionError(f"unexpected discovery call: {url}")
        self.calls.append(url)
        if url.endswith("/space/space-1/list"):
            return _FakeResponse({"lists": [{"id": "ops-list", "name": "Operations"}]})
        if url.endswith("/space/space-1/folder"):
            return _FakeResponse({"folders": [{"id": "folder-1", "name": "Shipment folders"}]})
        if url.endswith("/folder/folder-1/list"):
            return _FakeResponse(
                {
                    "lists": [
                        {"id": "valid-list", "name": "Grupo Master shipments"},
                        {"id": "bad-schema-list", "name": "Broken shipments"},
                    ]
                }
            )
        if url.endswith("/list/valid-list/field"):
            return _FakeResponse(
                {
                    "fields": [
                        {"id": "container-field"},
                        {"id": "booking-field"},
                        {"id": "carrier-field"},
                        {"id": "last-checked-field"},
                    ]
                }
            )
        if url.endswith("/list/bad-schema-list/field"):
            return _FakeResponse(
                {
                    "fields": [
                        {"id": "container-field"},
                        {"id": "booking-field"},
                        {"id": "carrier-field"},
                    ]
                }
            )
        raise AssertionError(f"unexpected URL: {url}")


def _task() -> dict[str, Any]:
    carrier_field = {
        "id": "carrier-field",
        "value": "one-option-id",
        "type_config": {
            "options": [
                {
                    "id": "one-option-id",
                    "name": "ONE",
                    "orderindex": 0,
                }
            ]
        },
    }
    return {
        "id": "task-1",
        "name": "Shipment",
        "status": {"type": "open"},
        "list": {"id": "list-1", "name": "Shipments"},
        "custom_fields": [
            carrier_field,
            {"id": "booking-field", "value": "BOOK-1"},
            {"id": "container-field", "value": "CONT-1"},
        ],
    }


def test_list_shipments_prefilters_clickup_tasks_for_single_allowed_carrier() -> None:
    client = ClickUpClient(_settings(shipment_allowed_lines=["one"]))
    fake_session = _FakeSession(
        task_responses=[
            _FakeResponse({"tasks": [_task()], "last_page": True}),
        ]
    )
    client.session = fake_session  # type: ignore[assignment]

    shipments = client.list_shipments()

    assert [shipment.task_id for shipment in shipments] == ["task-1"]
    task_calls = [call for call in fake_session.calls if call[0].endswith("/task")]
    assert len(task_calls) == 1
    params = task_calls[0][1]
    assert params is not None
    custom_fields = json.loads(params["custom_fields"])
    assert custom_fields == [
        {
            "field_id": "carrier-field",
            "operator": "=",
            "value": "one-option-id",
        }
    ]


def test_list_shipments_does_not_prefilter_multi_carrier_runs() -> None:
    client = ClickUpClient(_settings(shipment_allowed_lines=["one", "msc"]))
    fake_session = _FakeSession(
        task_responses=[
            _FakeResponse({"tasks": [_task()], "last_page": True}),
        ]
    )
    client.session = fake_session  # type: ignore[assignment]

    shipments = client.list_shipments()

    assert [shipment.task_id for shipment in shipments] == ["task-1"]
    task_calls = [call for call in fake_session.calls if call[0].endswith("/task")]
    assert task_calls[0][1] is not None
    assert "custom_fields" not in task_calls[0][1]


def test_list_shipments_falls_back_when_clickup_rejects_prefilter() -> None:
    client = ClickUpClient(_settings(shipment_allowed_lines=["one"]))
    fake_session = _FakeSession(
        task_responses=[
            _FakeResponse(
                {},
                error=requests.HTTPError("500 Server Error"),
            ),
            _FakeResponse({"tasks": [_task()], "last_page": True}),
        ]
    )
    client.session = fake_session  # type: ignore[assignment]

    shipments = client.list_shipments()

    assert [shipment.task_id for shipment in shipments] == ["task-1"]
    task_calls = [call for call in fake_session.calls if call[0].endswith("/task")]
    assert task_calls[0][1] is not None
    assert "custom_fields" in task_calls[0][1]
    assert task_calls[1][1] is not None
    assert "custom_fields" not in task_calls[1][1]


def test_discovered_lists_are_schema_validated_and_cached(tmp_path) -> None:
    cache_path = tmp_path / "clickup-lists.json"
    client = ClickUpClient(
        _settings(
            clickup_list_ids=["base-list"],
            clickup_space_ids=["space-1"],
            clickup_discover_from_spaces=True,
            cf_status_last_checked="last-checked-field",
            clickup_discovery_list_name_include=["shipments"],
            clickup_discovery_cache_path=str(cache_path),
        )
    )
    discovery_session = _DiscoverySession()
    client.session = discovery_session  # type: ignore[assignment]

    target_lists = client._resolve_target_lists()

    assert target_lists == {
        "base-list": "",
        "valid-list": "Grupo Master shipments",
    }
    assert any(call.endswith("/list/valid-list/field") for call in discovery_session.calls)
    assert any(call.endswith("/list/bad-schema-list/field") for call in discovery_session.calls)
    assert not any(call.endswith("/list/ops-list/field") for call in discovery_session.calls)

    cached_client = ClickUpClient(
        _settings(
            clickup_list_ids=["base-list"],
            clickup_space_ids=["space-1"],
            clickup_discover_from_spaces=True,
            cf_status_last_checked="last-checked-field",
            clickup_discovery_list_name_include=["shipments"],
            clickup_discovery_cache_path=str(cache_path),
        )
    )
    cached_client.session = _DiscoverySession(fail_on_get=True)  # type: ignore[assignment]

    assert cached_client._resolve_target_lists() == {
        "base-list": "",
        "valid-list": "Grupo Master shipments",
    }
