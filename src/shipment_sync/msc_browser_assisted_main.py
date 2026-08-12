from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import Settings
from shipment_sync.main import _filter_shipments, _print_preview_plan
from shipment_sync.msc_browser_assisted import build_queue, is_msc_line, status_from_browser_capture, write_queue
from shipment_sync.terminal import terminal_safe_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare and validate operator-assisted MSC tracking captures")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export-queue", type=Path, help="Write an MSC browser review queue as JSON. No ClickUp writes.")
    mode.add_argument("--preview-capture", type=Path, help="Parse copied MSC result text and preview its ClickUp projection.")
    parser.add_argument("--task-id", help="Restrict queue or preview to one internal ClickUp task ID.")
    parser.add_argument("--booking", help="Restrict queue or preview to one booking number.")
    parser.add_argument("--container", help="Restrict queue or preview to one container number.")
    args = parser.parse_args()

    load_dotenv()
    settings = Settings.from_env()
    client = ClickUpClient(settings)
    shipments = _filter_shipments(
        client.list_shipments(), task_id=args.task_id, booking=args.booking, container=args.container
    )
    msc_shipments = [shipment for shipment in shipments if is_msc_line(shipment.shipping_line)]

    if args.export_queue:
        items = build_queue(msc_shipments)
        write_queue(args.export_queue, items)
        print(terminal_safe_text(f"Wrote {len(items)} MSC browser review item(s) to {args.export_queue}"))
        return

    if len(msc_shipments) != 1:
        raise ValueError("Preview capture requires exactly one matching MSC shipment")
    capture_text = args.preview_capture.read_text(encoding="utf-8")
    status = status_from_browser_capture(capture_text)
    plan = client.plan_shipment_update(msc_shipments[0], status)
    _print_preview_plan(msc_shipments[0], plan)
    print("No ClickUp write was made.")


if __name__ == "__main__":
    main()
