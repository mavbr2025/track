import argparse
import sys

from dotenv import load_dotenv
import requests

from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import Settings
from shipment_sync.date_utils import format_display_date
from shipment_sync.sync import run_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync shipment status to ClickUp")
    parser.add_argument("--dry-run", action="store_true", help="List shipments without updating")
    args = parser.parse_args()

    load_dotenv()
    try:
        settings = Settings.from_env()
        client = ClickUpClient(settings)

        if args.dry_run:
            shipments = client.list_shipments()
            print(f"Found {len(shipments)} candidate shipment tasks")

            by_list: dict[str, int] = {}
            for s in shipments:
                label = f"{s.list_name} ({s.list_id})" if s.list_name else s.list_id
                by_list[label] = by_list.get(label, 0) + 1
                print(
                    f"- [{label}] {s.task_name} | line={s.shipping_line} | "
                    f"booking={s.booking_no} | container={s.container_no}"
                )

            if by_list:
                print("Candidates by list:")
                for label, count in sorted(by_list.items()):
                    print(f"- {label}: {count}")
            return

        stats = run_sync(client)
        updated_items = stats.updated_items
        skipped = stats.skipped
        print(f"Sync complete. Candidates: {stats.total_candidates}, Updated: {len(updated_items)}, skipped: {skipped}")

        if stats.candidates_by_list:
            print("Candidates by list:")
            for label, count in sorted(stats.candidates_by_list.items()):
                print(f"- {label}: {count}")

        if stats.updated_by_list:
            print("Updated by list:")
            for label, count in sorted(stats.updated_by_list.items()):
                print(f"- {label}: {count}")

        if updated_items:
            print("Updated shipments:")
            for item in updated_items:
                eta_text = _format_date(item.eta_local_text, item.eta_time)
                label = f"{item.list_name} ({item.list_id})" if item.list_name else item.list_id
                if settings.eta_only_mode:
                    latest_move_bits: list[str] = []
                    if item.latest_move_name:
                        latest_move_bits.append(f"latest_move={item.latest_move_name}")
                    if item.latest_move_time_local_text:
                        latest_move_bits.append(f"latest_move_time={_format_date(item.latest_move_time_local_text, None)}")
                    if item.latest_move_location:
                        latest_move_bits.append(f"latest_move_location={item.latest_move_location}")
                    latest_move_suffix = f" | {' | '.join(latest_move_bits)}" if latest_move_bits else ""
                    print(
                        f"- [{label}] {item.task_name} | line={item.shipping_line} | "
                        f"booking={item.booking_no} | container={item.container_no} | "
                        f"eta_local={eta_text}{latest_move_suffix}"
                    )
                else:
                    time_text = item.event_time.date().isoformat() if item.event_time else "n/a"
                    movement_text = item.movement_details if item.movement_details else "n/a"
                    location_text = item.location if item.location else "n/a"
                    print(
                        f"- [{label}] {item.task_name} | line={item.shipping_line} | "
                        f"booking={item.booking_no} | container={item.container_no} | "
                        f"status={item.status_text} | location={location_text} | "
                        f"last_move_time={time_text} | eta_local={eta_text} | "
                        f"last_move={movement_text}"
                    )
    except requests.HTTPError as exc:
        _print_http_error(exc)
        raise SystemExit(1) from exc
    except requests.RequestException as exc:
        print(f"Network request failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def _format_date(local_text: str | None, event_time) -> str:
    return format_display_date(local_text, event_time)


def _print_http_error(exc: requests.HTTPError) -> None:
    response = exc.response
    if response is None:
        print(f"HTTP request failed: {exc}", file=sys.stderr)
        return

    url = response.url or "unknown URL"
    status_code = response.status_code
    print(f"HTTP {status_code} while calling {url}", file=sys.stderr)

    if status_code == 401 and "api.clickup.com" in url:
        print("ClickUp authorization failed.", file=sys.stderr)
        print(
            "Check CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN in .env "
            "and confirm that credential can access the configured list/space.",
            file=sys.stderr,
        )
        print("If this computer should only use a specific list, set CLICKUP_DISCOVER_LISTS_FROM_SPACES=false.", file=sys.stderr)
        return

    snippet = (response.text or "").strip().replace("\n", " ")[:240]
    if snippet:
        print(f"Response snippet: {snippet}", file=sys.stderr)


if __name__ == "__main__":
    main()
