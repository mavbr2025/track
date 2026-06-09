from decimal import Decimal

from shipment_sync.clickup_pricing_client import extract_clickup_task_token
from shipment_sync.pricing_sync_main import _field_is_allowed, _normalize_token, _transform_field_value
from shipment_sync.pricing_sync import find_quote_for_shipment, run_bulk_pricing_sync, sync_pricing_pair
from shipment_sync.pricing_sync_config import PricingSyncSettings


def test_extract_clickup_task_token_from_custom_id_url() -> None:
    assert extract_clickup_task_token("https://app.clickup.com/t/8451352/MTMQUOTE-3404") == "MTMQUOTE-3404"


def test_extract_clickup_task_token_from_native_id_url() -> None:
    assert extract_clickup_task_token("https://app.clickup.com/t/86e07a995") == "86e07a995"


def test_field_allow_list_accepts_pricing_field_name() -> None:
    field = {"id": "field-1", "name": "Freight Vendor"}
    assert _field_is_allowed(field, {_normalize_token("Freight Vendor")}) is True


def test_field_allow_list_rejects_operational_field_name() -> None:
    field = {"id": "field-2", "name": "Number of Containers"}
    assert _field_is_allowed(field, {_normalize_token("Freight Vendor")}) is False


def test_currency_field_is_scaled_by_shipment_container_count() -> None:
    value, transform = _transform_field_value(
        quote_field={"name": "Freight (Ocean/Truck/Air)", "type": "currency"},
        source_value="2500",
        shipment_container_count=Decimal("5"),
    )
    assert value == "12500"
    assert transform is not None


def test_non_currency_field_is_not_scaled() -> None:
    value, transform = _transform_field_value(
        quote_field={"name": "Free Days Contracted", "type": "number"},
        source_value="21",
        shipment_container_count=Decimal("5"),
    )
    assert value == "21"
    assert transform is None


def test_customs_broker_field_is_not_scaled_by_container_count() -> None:
    value, transform = _transform_field_value(
        quote_field={"name": "-Cost- Customs agent", "type": "currency"},
        source_value="1030",
        shipment_container_count=Decimal("5"),
    )
    assert value == "1030"
    assert transform is None


def test_doc_fee_destination_is_scaled_when_doc_fee_applies_per_container() -> None:
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
            {"id": "doc-fee-dest", "name": "Doc Fee Destination", "value": ""},
            {"id": "containers", "name": "Number of Containers", "value": "5"},
        ],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3432",
        "name": "Quote Task",
        "custom_fields": [
            {
                "id": "be293be3-8b59-477a-87c1-5a4f36a225ac",
                "name": "Doc fee applies:",
                "type": "drop_down",
                "type_config": {
                    "options": [
                        {"id": "per-bl", "name": "Per BL", "orderindex": 0},
                        {"id": "per-container", "name": "Per Container", "orderindex": 1},
                    ]
                },
                "value": 1,
            },
            {"id": "doc-fee-dest", "name": "Doc Fee Destination", "type": "currency", "value": "100"},
        ],
    }

    result = sync_pricing_pair(
        client=type("FakePricingClient", (), {"update_custom_field": lambda *args, **kwargs: None})(),
        settings=PricingSyncSettings(
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
        ),
        shipment_task=shipment_task,
        quote_task=quote_task,
        dry_run=True,
        overwrite_existing=False,
    )

    assert result["updates"][0]["field_name"] == "Doc Fee Destination"
    assert result["updates"][0]["value"] == "500"


def test_doc_fee_destination_is_not_scaled_when_doc_fee_applies_per_bl() -> None:
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
            {"id": "doc-fee-dest", "name": "Doc Fee Destination", "value": ""},
            {"id": "containers", "name": "Number of Containers", "value": "5"},
        ],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3432",
        "name": "Quote Task",
        "custom_fields": [
            {
                "id": "be293be3-8b59-477a-87c1-5a4f36a225ac",
                "name": "Doc fee applies:",
                "type": "drop_down",
                "type_config": {
                    "options": [
                        {"id": "per-bl", "name": "Per BL", "orderindex": 0},
                        {"id": "per-container", "name": "Per Container", "orderindex": 1},
                    ]
                },
                "value": 0,
            },
            {"id": "doc-fee-dest", "name": "Doc Fee Destination", "type": "currency", "value": "100"},
        ],
    }

    result = sync_pricing_pair(
        client=type("FakePricingClient", (), {"update_custom_field": lambda *args, **kwargs: None})(),
        settings=PricingSyncSettings(
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
        ),
        shipment_task=shipment_task,
        quote_task=quote_task,
        dry_run=True,
        overwrite_existing=False,
    )

    assert result["updates"][0]["field_name"] == "Doc Fee Destination"
    assert result["updates"][0]["value"] == "100"


def test_bulk_sync_matches_quote_via_shipment_associated_relationship() -> None:
    class FakePricingClient:
        def __init__(self, shipment_task: dict, quote_task: dict) -> None:
            self.shipment_task = shipment_task
            self.quote_task = quote_task

        def list_tasks(self, list_ids: list[str]) -> list[dict]:
            if "ship-list" in list_ids:
                return [self.shipment_task]
            if "quote-list" in list_ids:
                return [self.quote_task]
            return []

        def update_custom_field(self, task_id: str, field_id: str, value: object) -> None:
            raise AssertionError("dry-run should not write fields")

    settings = PricingSyncSettings(
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
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
            {"id": "freight-field", "name": "Freight (Ocean/Truck/Air)", "value": ""},
            {"id": "shipment-associated", "name": "Shipment associated", "value": None},
            {"id": "mtm-booking", "name": "MTM Booking", "value": "MTMLXGT-25717"},
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

    result = run_bulk_pricing_sync(
        FakePricingClient(shipment_task, quote_task),
        settings,
        dry_run=True,
        overwrite_existing=False,
    )

    assert result["shipments_matched"] == 1
    assert result["shipments_updated"] == 1
    assert result["shipments_skipped"] == 0
    assert result["results"][0]["quote_custom_id"] == "MTMQUOTE-3432"
    assert result["results"][0]["match_value"] == "MTMLXGT-25717"
    assert result["results"][0]["applied_updates"] == 2


def test_find_quote_for_shipment_prefers_linked_task_relationship() -> None:
    class FakePricingClient:
        def list_tasks(self, list_ids: list[str]) -> list[dict]:
            raise AssertionError("preloaded quotes should be used")

    settings = PricingSyncSettings(
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
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "linked_tasks": [
            {"task_id": "quote-task-1", "link_id": "shipment-task-1"},
        ],
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
            {"id": "mtm-booking", "name": "MTM Booking", "value": "MTMLXGT-25717"},
        ],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3432",
        "name": "Quote Task",
        "custom_fields": [
            {"id": "shipment-associated", "name": "Shipment associated", "type": "tasks", "value": []},
        ],
    }

    matched_quote, matched_on, matched_value = find_quote_for_shipment(
        FakePricingClient(),
        settings,
        shipment_task=shipment_task,
        preloaded_quotes=[quote_task],
    )

    assert matched_quote == quote_task
    assert matched_on == "linked_task"
    assert matched_value == "quote-task-1"


def test_find_quote_for_shipment_fetches_linked_quote_before_listing_quotes() -> None:
    class FakePricingClient:
        def __init__(self, quote_task: dict) -> None:
            self.quote_task = quote_task

        def get_task(self, task_ref: str) -> dict:
            assert task_ref == "quote-task-1"
            return self.quote_task

        def list_tasks(self, list_ids: list[str]) -> list[dict]:
            raise AssertionError("linked quote lookup should avoid listing the quote list")

    settings = PricingSyncSettings(
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
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "linked_tasks": [
            {"task_id": "quote-task-1", "link_id": "shipment-task-1"},
        ],
        "custom_fields": [],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3432",
        "name": "Quote Task",
        "list": {"id": "quote-list", "name": "Quotes"},
        "custom_fields": [],
    }

    matched_quote, matched_on, matched_value = find_quote_for_shipment(
        FakePricingClient(quote_task),
        settings,
        shipment_task=shipment_task,
    )

    assert matched_quote == quote_task
    assert matched_on == "linked_task"
    assert matched_value == "quote-task-1"


def test_sync_pair_prefers_quote_custom_id_over_quote_match_field_value() -> None:
    class FakePricingClient:
        def update_custom_field(self, task_id: str, field_id: str, value: object) -> None:
            raise AssertionError("dry-run should not write fields")

    settings = PricingSyncSettings(
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
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
            {"id": "freight-field", "name": "Freight (Ocean/Truck/Air)", "value": ""},
            {"id": "containers", "name": "Number of Containers", "value": "1"},
        ],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3432",
        "name": "Quote Task",
        "custom_fields": [
            {"id": "quote-number", "name": "MTM Quote #", "type": "short_text", "value": "86dzvvtyp"},
            {"id": "freight-field", "name": "Freight (Ocean/Truck/Air)", "type": "currency", "value": "2500"},
        ],
    }

    result = sync_pricing_pair(
        FakePricingClient(),
        settings,
        shipment_task=shipment_task,
        quote_task=quote_task,
        dry_run=True,
        overwrite_existing=False,
    )

    assert result["applied_updates"] == 2
    assert result["updates"][0]["field_name"] == "Freight (Ocean/Truck/Air)"
    assert result["updates"][1]["field_name"] == "MTM Quote #"
    assert result["updates"][1]["value"] == "MTMQUOTE-3432"


def test_sync_pair_matches_target_field_by_name_when_ids_differ() -> None:
    class FakePricingClient:
        def update_custom_field(self, task_id: str, field_id: str, value: object) -> None:
            raise AssertionError("dry-run should not write fields")

    settings = PricingSyncSettings(
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
    shipment_task = {
        "id": "shipment-task-1",
        "custom_id": "MTMLXGT-25717",
        "name": "Shipment Task",
        "custom_fields": [
            {"id": "shipment-inland-currency", "name": "💰 Inland Currency", "type": "drop_down", "value": None},
            {"id": "quote-number", "name": "MTM Quote #", "value": ""},
        ],
    }
    quote_task = {
        "id": "quote-task-1",
        "custom_id": "MTMQUOTE-3432",
        "name": "Quote Task",
        "custom_fields": [
            {"id": "quote-inland-currency", "name": "💰 Inland Currency", "type": "drop_down", "value": 0},
        ],
    }

    result = sync_pricing_pair(
        FakePricingClient(),
        settings,
        shipment_task=shipment_task,
        quote_task=quote_task,
        dry_run=True,
        overwrite_existing=False,
    )

    assert result["applied_updates"] == 2
    assert result["updates"][0]["field_id"] == "shipment-inland-currency"
    assert result["updates"][0]["field_name"] == "💰 Inland Currency"
    assert result["updates"][0]["value"] == 0
    assert result["updates"][1]["field_name"] == "MTM Quote #"
    assert result["updates"][1]["value"] == "MTMQUOTE-3432"
