from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from dotenv import load_dotenv
import requests

from .clickup_pricing_client import ClickUpPricingClient
from .pricing_sync_config import PricingSyncSettings
from .pricing_sync import (
    _allowed_copy_selectors,
    _coerce_field_value,
    _field_by_selector,
    _field_is_allowed,
    _field_map,
    _is_empty_value,
    _normalize_token,
    _parse_decimal,
    _render_decimal,
    _shipment_container_count,
    _transform_field_value,
    find_quote_for_shipment,
    run_bulk_pricing_sync,
    sync_pricing_pair,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync pricing and procurement fields from ClickUp quote tasks into shipment tasks")
    parser.add_argument("--shipment", help="Shipment task ref or URL.")
    parser.add_argument("--quote", help="Quote task ref or URL. Optional when quote discovery is configured.")
    parser.add_argument(
        "--sync-linked-shipments",
        action="store_true",
        help="Bulk sync shipments from CLICKUP_LIST_ID(S) using the shipment match field to find quote tasks.",
    )
    parser.add_argument("--overwrite-existing", action="store_true", help="Overwrite non-empty shipment pricing fields.")
    parser.add_argument("--dry-run", action="store_true", help="Preview field updates without writing to ClickUp.")
    args = parser.parse_args()

    if args.sync_linked_shipments:
        if args.shipment or args.quote:
            parser.error("Use either --sync-linked-shipments or an explicit --shipment/--quote pair.")
    elif not args.shipment:
        parser.error("Provide --shipment, optionally --quote, or use --sync-linked-shipments.")

    load_dotenv()
    try:
        settings = PricingSyncSettings.from_env()
        client = ClickUpPricingClient(settings)
        overwrite_existing = args.overwrite_existing or not settings.clickup_pricing_only_empty_targets

        if args.sync_linked_shipments:
            _run_bulk_sync(client, settings, dry_run=args.dry_run, overwrite_existing=overwrite_existing)
            return

        shipment_task = client.get_task(args.shipment or "")
        if args.quote:
            quote_task = client.get_task(args.quote)
        else:
            quote_task, matched_on, matched_value = find_quote_for_shipment(
                client,
                settings,
                shipment_task=shipment_task,
            )
            if quote_task is None:
                if matched_on and matched_value is None:
                    raise ValueError(f"Could not discover quote for shipment using {matched_on}.")
                raise ValueError("Could not discover quote for shipment.")
        result = sync_pricing_pair(
            client,
            settings,
            shipment_task=shipment_task,
            quote_task=quote_task,
            dry_run=args.dry_run,
            overwrite_existing=overwrite_existing,
        )
        if not args.quote:
            result["match_selector"] = matched_on
            result["match_value"] = matched_value
        _print_pair_result(result)
    except requests.HTTPError as exc:
        _print_http_error(exc)
        raise SystemExit(1) from exc
    except requests.RequestException as exc:
        print(f"Network request failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def _run_bulk_sync(
    client: ClickUpPricingClient,
    settings: PricingSyncSettings,
    *,
    dry_run: bool,
    overwrite_existing: bool,
) -> None:
    summary = run_bulk_pricing_sync(
        client,
        settings,
        dry_run=dry_run,
        overwrite_existing=overwrite_existing,
    )
    for result in summary["results"]:
        if result.get("skip_reason"):
            shipment_label = result.get("shipment_custom_id") or result.get("shipment_task_id") or "unknown-shipment"
            print(f"Skip shipment {shipment_label}: {result['skip_reason']}", file=sys.stderr)
            continue
        _print_pair_result(result)
    print(f"Shipments matched: {summary['shipments_matched']}")
    print(f"Shipments updated: {summary['shipments_updated']}")
    print(f"Shipments skipped: {summary['shipments_skipped']}")


def _print_pair_result(result: dict[str, Any]) -> None:
    shipment_label = result.get("shipment_custom_id") or result.get("shipment_task_id") or "unknown-shipment"
    quote_label = result.get("quote_custom_id") or result.get("quote_task_id") or "unknown-quote"
    mode_label = "DRY RUN" if result.get("dry_run") else "UPDATED"
    print(f"{mode_label} | shipment={shipment_label} | quote={quote_label} | fields={result['applied_updates']}")
    if result.get("match_selector") and result.get("match_value"):
        print(f"- matched via {result['match_selector']} = {json.dumps(result['match_value'], ensure_ascii=True)}")
    for update in result["updates"]:
        rendered = json.dumps(update["value"], ensure_ascii=True, default=str)
        detail = ""
        transform = update.get("transform")
        if transform:
            detail = f" | from {transform}"
        print(f"- {update['field_name']} = {rendered}{detail}")


def _print_http_error(exc: requests.HTTPError) -> None:
    response = exc.response
    if response is None:
        print(f"HTTP request failed: {exc}", file=sys.stderr)
        return

    url = response.url or "unknown URL"
    status_code = response.status_code
    body = response.text or ""
    print(f"HTTP {status_code} while calling {url}", file=sys.stderr)

    if "ACCESS_606" in body or "INSUFFICIENT_PARENT_" in body:
        print(
            "ClickUp denied the write. The configured token can read the task, "
            "but it does not have permission to update one or more destination custom fields.",
            file=sys.stderr,
        )

    snippet = body.strip().replace("\n", " ")[:240]
    if snippet:
        print(f"Response snippet: {snippet}", file=sys.stderr)


if __name__ == "__main__":
    main()
