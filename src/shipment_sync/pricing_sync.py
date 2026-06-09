from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .clickup_pricing_client import ClickUpPricingClient
from .pricing_sync_config import PricingSyncSettings

DEFAULT_PRICING_FIELD_NAMES = {
    "- cost - insurance",
    "-cost- ct / handling fee",
    "-cost- customs agent",
    "-cost- customs broker origin",
    "-cost- destination charges",
    "-cost- doc fee dest",
    "-cost- doc fee origin",
    "-cost- freight",
    "-cost- inland destination",
    "-cost- inland origin",
    "-cost- origin charges",
    "cargo maritime insurance",
    "ct / handling fee",
    "ct / handling vendor",
    "customs agent vendor",
    "customs broker origin",
    "destination charges",
    "destination charges vendor",
    "doc fee dest vendor",
    "doc fee destination",
    "doc fee origin",
    "doc fee origin vendor",
    "free days contracted",
    "freight (ocean/truck/air)",
    "freight vendor",
    "💰 customs broker currency",
    "💰 customs broker currency code",
    "💰 customs broker origin vendor",
    "💰 inland currency",
    "💰 inland currency code",
    "inland destination",
    "inland destination vendor",
    "inland origin",
    "inland origin vendor",
    "mtm quote #",
    "origin charges",
    "origin charges vendor",
}
SHIPMENT_CONTAINER_COUNT_FIELD = "Number of Containers"
DOC_FEE_APPLIES_FIELD_ID = "be293be3-8b59-477a-87c1-5a4f36a225ac"
DOC_FEE_APPLIES_FIELD_NAME = "Doc fee applies:"
DOC_FEE_PER_BL = "per bl"
DOC_FEE_PER_CONTAINER = "per container"
DOC_FEE_DESTINATION_FIELDS = {
    "-cost- doc fee dest",
    "doc fee destination",
}
CUSTOMS_BROKER_PER_SHIPMENT_FIELDS = {
    "-cost- customs agent",
    "-cost- customs broker origin",
    "customs broker origin",
}


def run_bulk_pricing_sync(
    client: ClickUpPricingClient,
    settings: PricingSyncSettings,
    *,
    dry_run: bool,
    overwrite_existing: bool,
) -> dict[str, Any]:
    if not settings.clickup_shipment_list_ids:
        raise ValueError("Bulk sync requires CLICKUP_LIST_ID or CLICKUP_LIST_IDS.")
    if not settings.clickup_pricing_list_ids:
        raise ValueError("Bulk sync requires CLICKUP_PRICING_LIST_ID or CLICKUP_PRICING_LIST_IDS.")

    shipment_tasks = client.list_tasks(settings.clickup_shipment_list_ids)
    quote_tasks = client.list_tasks(settings.clickup_pricing_list_ids)
    quotes_by_ref = _index_quotes(quote_tasks, settings)

    processed = 0
    updated = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    for shipment_task in shipment_tasks:
        quote_task, matched_on, matched_value = find_quote_for_shipment(
            client,
            settings,
            shipment_task=shipment_task,
            preloaded_quotes=quote_tasks,
            quote_index=quotes_by_ref,
        )
        if quote_task is None:
            skipped += 1
            skip_reason = "no quote found"
            if matched_value:
                skip_reason = f"no quote found for {matched_value}"
            results.append(
                {
                    "shipment_task_id": shipment_task.get("id"),
                    "shipment_custom_id": shipment_task.get("custom_id"),
                    "shipment_name": shipment_task.get("name"),
                    "quote_task_id": None,
                    "quote_custom_id": None,
                    "quote_name": None,
                    "dry_run": dry_run,
                    "applied_updates": 0,
                    "updates": [],
                    "match_selector": matched_on,
                    "match_value": matched_value,
                    "skip_reason": skip_reason,
                }
            )
            continue

        processed += 1
        result = sync_pricing_pair(
            client,
            settings,
            shipment_task=shipment_task,
            quote_task=quote_task,
            dry_run=dry_run,
            overwrite_existing=overwrite_existing,
        )
        result["match_selector"] = matched_on
        result["match_value"] = matched_value
        results.append(result)
        if result["applied_updates"] > 0:
            updated += 1

    return {
        "shipments_matched": processed,
        "shipments_updated": updated,
        "shipments_skipped": skipped,
        "results": results,
    }


def find_quote_for_shipment(
    client: ClickUpPricingClient,
    settings: PricingSyncSettings,
    *,
    shipment_task: dict[str, Any],
    preloaded_quotes: list[dict[str, Any]] | None = None,
    quote_index: dict[str, tuple[dict[str, Any], str]] | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    linked_task_ids = _linked_task_ids(shipment_task)
    if quote_index is not None and linked_task_ids:
        for related_task_id in linked_task_ids:
            matched = quote_index.get(_normalize_match_key(related_task_id))
            if matched is not None:
                quote_task, _ = matched
                return quote_task, "linked_task", related_task_id

    if preloaded_quotes is None and linked_task_ids:
        for related_task_id in linked_task_ids:
            try:
                related_task = client.get_task(related_task_id)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    continue
                raise
            if _task_is_quote_candidate(related_task, settings):
                return related_task, "linked_task", related_task_id

    if not settings.clickup_pricing_list_ids:
        raise ValueError("Quote discovery requires CLICKUP_PRICING_LIST_ID or CLICKUP_PRICING_LIST_IDS.")

    quote_tasks = preloaded_quotes if preloaded_quotes is not None else client.list_tasks(settings.clickup_pricing_list_ids)
    indexed_quotes = quote_index if quote_index is not None else _index_quotes(quote_tasks, settings)

    for related_task_id in linked_task_ids:
        matched = indexed_quotes.get(_normalize_match_key(related_task_id))
        if matched is not None:
            quote_task, _ = matched
            return quote_task, "linked_task", related_task_id

    candidates = _shipment_match_candidates(shipment_task, settings)
    if not candidates:
        return None, None, None

    for selector, candidate in candidates:
        match_key = _normalize_match_key(candidate)
        if not match_key:
            continue
        matched = indexed_quotes.get(match_key)
        if matched is not None:
            quote_task, _ = matched
            return quote_task, selector, candidate
    unmatched_selector, unmatched_value = next(
        (
            (selector, candidate)
            for selector, candidate in candidates
            if selector not in {"shipment_custom_id", "shipment_task_id"}
        ),
        candidates[0],
    )
    return None, unmatched_selector, unmatched_value


def sync_pricing_pair(
    client: ClickUpPricingClient,
    settings: PricingSyncSettings,
    *,
    shipment_task: dict[str, Any],
    quote_task: dict[str, Any],
    dry_run: bool,
    overwrite_existing: bool,
) -> dict[str, Any]:
    shipment_custom_fields = shipment_task.get("custom_fields", [])
    shipment_fields = _field_map(shipment_custom_fields)
    quote_fields = _field_map(quote_task.get("custom_fields", []))
    allowed_selectors = _allowed_copy_selectors(settings)
    shipment_container_count = _shipment_container_count(shipment_custom_fields)
    doc_fee_applies_mode = _doc_fee_applies_mode(quote_task.get("custom_fields", []))

    updates: list[dict[str, Any]] = []
    update_field_ids: set[str] = set()
    for field_id, quote_field in quote_fields.items():
        shipment_field = shipment_fields.get(field_id)
        if shipment_field is None:
            shipment_field = _field_by_selector(shipment_custom_fields, str(quote_field.get("name") or ""))
        if shipment_field is None:
            continue
        if not _field_is_allowed(quote_field, allowed_selectors):
            continue
        if settings.clickup_pricing_set_quote_number and _field_matches_selector(
            quote_field,
            settings.clickup_pricing_match_field,
        ):
            # The quote's match field can store an internal task token, while the shipment
            # should receive the quote task custom ID via the explicit quote-number step below.
            continue
        source_value = quote_field.get("value")
        if _is_empty_value(source_value):
            continue
        target_value, transform = _transform_field_value(
            quote_field=quote_field,
            source_value=source_value,
            shipment_container_count=shipment_container_count,
            doc_fee_applies_mode=doc_fee_applies_mode,
        )

        shipment_value = shipment_field.get("value")
        if not overwrite_existing and not _is_empty_value(shipment_value):
            continue
        if shipment_value == target_value:
            continue

        _append_update(
            updates,
            update_field_ids,
            field_id=str(shipment_field.get("id") or field_id),
            field_name=str(quote_field.get("name") or shipment_field.get("name") or field_id),
            value=target_value,
            source_value=source_value,
            existing_value=shipment_value,
            transform=transform,
        )

    if settings.clickup_pricing_set_quote_number:
        quote_number_field = _field_by_selector(shipment_task.get("custom_fields", []), settings.clickup_pricing_match_field)
        quote_custom_id = str(quote_task.get("custom_id") or "").strip()
        if quote_number_field and quote_custom_id:
            current_value = quote_number_field.get("value")
            if current_value != quote_custom_id and (overwrite_existing or _is_empty_value(current_value)):
                _append_update(
                    updates,
                    update_field_ids,
                    field_id=str(quote_number_field.get("id") or ""),
                    field_name=str(quote_number_field.get("name") or settings.clickup_pricing_match_field),
                    value=quote_custom_id,
                    source_value=quote_custom_id,
                    existing_value=current_value,
                    transform=None,
                )

    if not dry_run:
        for update in updates:
            client.update_custom_field(shipment_task["id"], update["field_id"], update["value"])

    return {
        "shipment_task_id": shipment_task.get("id"),
        "shipment_custom_id": shipment_task.get("custom_id"),
        "shipment_name": shipment_task.get("name"),
        "quote_task_id": quote_task.get("id"),
        "quote_custom_id": quote_task.get("custom_id"),
        "quote_name": quote_task.get("name"),
        "dry_run": dry_run,
        "applied_updates": len(updates),
        "updates": updates,
        "match_selector": None,
        "match_value": None,
        "skip_reason": None,
    }


def _index_quotes(tasks: list[dict[str, Any]], settings: PricingSyncSettings) -> dict[str, tuple[dict[str, Any], str]]:
    out: dict[str, tuple[dict[str, Any], str]] = {}
    for task in tasks:
        for selector, candidate in _quote_match_candidates(task, settings):
            value = _normalize_match_key(candidate)
            if value and value not in out:
                out[value] = (task, selector)
    return out


def _quote_match_candidates(task: dict[str, Any], settings: PricingSyncSettings) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for selector in ("quote_custom_id", "quote_task_id"):
        value = task.get("custom_id") if selector == "quote_custom_id" else task.get("id")
        if value:
            _append_candidate(candidates, selector, str(value))
    for selector in settings.clickup_pricing_quote_match_fields:
        field = _field_by_selector(task.get("custom_fields", []), selector)
        for value in _coerce_field_values(field):
            _append_candidate(candidates, selector, value)
    return candidates


def _shipment_match_candidates(task: dict[str, Any], settings: PricingSyncSettings) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for selector in ("shipment_custom_id", "shipment_task_id"):
        value = task.get("custom_id") if selector == "shipment_custom_id" else task.get("id")
        if value:
            _append_candidate(candidates, selector, str(value))
    for selector in settings.clickup_pricing_shipment_match_fields:
        field = _field_by_selector(task.get("custom_fields", []), selector)
        for value in _coerce_field_values(field):
            _append_candidate(candidates, selector, value)
    return candidates


def _append_candidate(candidates: list[tuple[str, str]], selector: str, value: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        return
    entry = (selector, cleaned)
    if entry not in candidates:
        candidates.append(entry)


def _normalize_match_key(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().upper()


def _linked_task_ids(task: dict[str, Any]) -> list[str]:
    task_id = str(task.get("id") or "").strip()
    out: list[str] = []
    for entry in task.get("linked_tasks", []):
        if not isinstance(entry, dict):
            continue
        for key in ("task_id", "link_id"):
            value = str(entry.get(key) or "").strip()
            if not value or value == task_id or value in out:
                continue
            out.append(value)
    return out


def _task_is_quote_candidate(task: dict[str, Any], settings: PricingSyncSettings) -> bool:
    list_id = str((task.get("list") or {}).get("id") or "").strip()
    if list_id and list_id in settings.clickup_pricing_list_ids:
        return True
    custom_id = str(task.get("custom_id") or "").strip().upper()
    return custom_id.startswith("MTMQUOTE-")


def _append_update(
    updates: list[dict[str, Any]],
    update_field_ids: set[str],
    *,
    field_id: str,
    field_name: str,
    value: Any,
    source_value: Any,
    existing_value: Any,
    transform: str | None,
) -> None:
    if not field_id or field_id in update_field_ids:
        return
    updates.append(
        {
            "field_id": field_id,
            "field_name": field_name,
            "value": value,
            "source_value": source_value,
            "existing_value": existing_value,
            "transform": transform,
        }
    )
    update_field_ids.add(field_id)


def _shipment_container_count(custom_fields: Any) -> Decimal | None:
    field = _field_by_selector(custom_fields, SHIPMENT_CONTAINER_COUNT_FIELD)
    if not field:
        return None
    return _parse_decimal(field.get("value"))


def _transform_field_value(
    *,
    quote_field: dict[str, Any],
    source_value: Any,
    shipment_container_count: Decimal | None,
    doc_fee_applies_mode: str | None = None,
) -> tuple[Any, str | None]:
    field_type = str(quote_field.get("type") or "").strip().lower()
    field_name = str(quote_field.get("name") or "").strip()
    parsed_source = _parse_decimal(source_value)
    if (
        field_type == "currency"
        and shipment_container_count is not None
        and parsed_source is not None
        and _should_scale_currency_field(field_name, doc_fee_applies_mode)
    ):
        scaled = parsed_source * shipment_container_count
        return (
            _render_decimal(scaled),
            f"{field_name} per container {_render_decimal(parsed_source)} x "
            f"{_render_decimal(shipment_container_count)} containers",
        )
    return source_value, None


def _should_scale_currency_field(field_name: str, doc_fee_applies_mode: str | None) -> bool:
    normalized_name = _normalize_token(field_name)
    if normalized_name in CUSTOMS_BROKER_PER_SHIPMENT_FIELDS:
        return False
    if normalized_name not in DOC_FEE_DESTINATION_FIELDS:
        return True
    if doc_fee_applies_mode == DOC_FEE_PER_BL:
        return False
    if doc_fee_applies_mode == DOC_FEE_PER_CONTAINER:
        return True
    return True


def _doc_fee_applies_mode(custom_fields: Any) -> str | None:
    field = _field_by_selector(custom_fields, DOC_FEE_APPLIES_FIELD_ID)
    if field is None:
        field = _field_by_selector(custom_fields, DOC_FEE_APPLIES_FIELD_NAME)
    if field is None:
        return None
    return _dropdown_option_name(field)


def _dropdown_option_name(field: dict[str, Any]) -> str | None:
    raw_value = field.get("value")
    if raw_value is None:
        return None
    options = (field.get("type_config") or {}).get("options") or []
    for option in options:
        if not isinstance(option, dict):
            continue
        if option.get("orderindex") == raw_value:
            return _normalize_token(str(option.get("name") or ""))
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("id") or "").strip()
        if option_id and str(raw_value).strip() == option_id:
            return _normalize_token(str(option.get("name") or ""))
    if isinstance(raw_value, str):
        cleaned = raw_value.strip()
        return _normalize_token(cleaned) if cleaned else None
    return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _render_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f").rstrip("0").rstrip(".")


def _field_map(custom_fields: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(custom_fields, list):
        return {}
    return {
        str(field.get("id") or "").strip(): field
        for field in custom_fields
        if isinstance(field, dict) and str(field.get("id") or "").strip()
    }


def _allowed_copy_selectors(settings: PricingSyncSettings) -> set[str]:
    configured = settings.clickup_pricing_copy_fields
    if configured:
        return {_normalize_token(item) for item in configured if item.strip()}
    return set(DEFAULT_PRICING_FIELD_NAMES)


def _field_is_allowed(field: dict[str, Any], allowed_selectors: set[str]) -> bool:
    field_id = str(field.get("id") or "").strip()
    field_name = str(field.get("name") or "").strip()
    if field_id and field_id in allowed_selectors:
        return True
    return _normalize_token(field_name) in allowed_selectors


def _field_by_selector(custom_fields: Any, selector: str | None) -> dict[str, Any] | None:
    if not selector or not isinstance(custom_fields, list):
        return None
    selector_norm = _normalize_token(selector)
    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if _field_matches_selector(field, selector, selector_norm=selector_norm):
            return field
    return None


def _field_matches_selector(
    field: dict[str, Any],
    selector: str | None,
    *,
    selector_norm: str | None = None,
) -> bool:
    if not selector:
        return False
    field_id = str(field.get("id") or "").strip()
    field_name = str(field.get("name") or "").strip()
    normalized = selector_norm if selector_norm is not None else _normalize_token(selector)
    return selector == field_id or normalized == _normalize_token(field_name)


def _coerce_field_value(field: dict[str, Any] | None) -> str | None:
    values = _coerce_field_values(field)
    if not values:
        return None
    return ", ".join(values)


def _coerce_field_values(field: dict[str, Any] | None) -> list[str]:
    if not field:
        return []
    return _coerce_match_values(field.get("value"))


def _coerce_match_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        values: list[str] = []
        for key in ("custom_id", "id", "url", "value", "label"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                values.append(nested.strip())
        return _dedupe_preserve_order(values)
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_coerce_match_values(item))
        return _dedupe_preserve_order(values)
    rendered = str(value).strip()
    return [rendered] if rendered else []


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _is_empty_value(value: Any) -> bool:
    return value in (None, "", [], {})


def _normalize_token(value: str) -> str:
    return " ".join(value.lower().split())
