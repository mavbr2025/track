from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Iterable

from shipment_sync.carriers.msc import _status_from_payload
from shipment_sync.models import ShipmentRef, ShipmentStatus


MSC_TRACKING_URL = "https://www.msc.com/en/track-a-shipment"
_CONTAINER_RE = re.compile(r"\b[A-Z]{4}\d{7}\b", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


@dataclass(frozen=True)
class MscBrowserQueueItem:
    task_id: str
    task_name: str
    list_id: str
    list_name: str | None
    booking_no: str | None
    container_numbers: list[str]
    expected_container_count: int | None
    tracking_url: str


def build_queue(shipments: Iterable[ShipmentRef]) -> list[MscBrowserQueueItem]:
    """Build a local review queue. This function performs no carrier or ClickUp writes."""
    items: list[MscBrowserQueueItem] = []
    for shipment in shipments:
        if shipment.shipping_line.strip().lower() not in {"msc", "msc shipping line", "mediterranean shipping company"}:
            continue
        references = _container_references(shipment.container_no)
        if not references and not shipment.booking_no:
            continue
        items.append(
            MscBrowserQueueItem(
                task_id=shipment.task_id,
                task_name=shipment.task_name,
                list_id=shipment.list_id,
                list_name=shipment.list_name,
                booking_no=shipment.booking_no,
                container_numbers=references,
                expected_container_count=shipment.expected_container_count,
                tracking_url=MSC_TRACKING_URL,
            )
        )
    return items


def write_queue(path: Path, items: Iterable[MscBrowserQueueItem]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "operator-assisted-browser",
        "tracking_url": MSC_TRACKING_URL,
        "instructions": [
            "Open the normal MSC tracking page in an operator-controlled browser session.",
            "Search each listed container first, then the booking only if MSC requires it.",
            "Copy the complete visible tracking result into a local text file.",
            "Use msc-browser-assisted --preview-capture before any ClickUp update.",
            "Do not solve or bypass a CAPTCHA or access-control challenge programmatically.",
        ],
        "shipments": [asdict(item) for item in items],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def status_from_browser_capture(text: str) -> ShipmentStatus:
    """Parse an operator-copied MSC result into the existing MSC projection model.

    The parser intentionally accepts only a complete visible result containing a
    container identifier and a POD ETA. It never infers a missing itinerary.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    containers = _container_references(text)
    if not containers:
        raise ValueError("MSC browser capture does not contain a container number")

    eta = _value_after_label(lines, "POD ETA")
    if not eta or not _DATE_RE.fullmatch(eta):
        raise ValueError("MSC browser capture does not contain a valid POD ETA")

    events = _capture_events(lines)
    if not events:
        raise ValueError("MSC browser capture does not contain any dated tracking events")

    final_vessel = _final_eta_vessel(events)
    vessel_name, voyage = _split_vessel_voyage(final_vessel)
    payload = {
        "IsSuccess": True,
        "Data": {
            "BillOfLadings": [
                {
                    "ContainersInfo": [
                        {
                            "ContainerNumber": containers[0],
                            "PodEtaDate": eta,
                            "FinalPodVesselName": vessel_name,
                            "FinalPodVoyage": voyage,
                            "Events": events,
                        }
                    ]
                }
            ]
        },
    }
    status = _status_from_payload(
        payload=payload,
        source="msc-browser-assisted:capture",
        source_url=MSC_TRACKING_URL,
        eta_only_mode=True,
    )
    status.discovered_containers = containers
    # A visible page is reliable for movement facts but not a blanket authority
    # to replace a manually corrected container list.
    status.container_discovery_authoritative = False
    return status


def _container_references(value: str | None) -> list[str]:
    seen: set[str] = set()
    references: list[str] = []
    for match in _CONTAINER_RE.findall(value or ""):
        reference = match.upper()
        if reference not in seen:
            seen.add(reference)
            references.append(reference)
    return references


def _value_after_label(lines: list[str], label: str) -> str | None:
    normalized_label = label.casefold()
    for index, line in enumerate(lines[:-1]):
        if line.casefold() == normalized_label:
            return lines[index + 1]
    return None


def _capture_events(lines: list[str]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if not _DATE_RE.fullmatch(line):
            continue
        if index + 2 >= len(lines):
            continue
        location = lines[index + 1]
        description = lines[index + 2]
        if not _looks_like_event_description(description):
            continue
        vessel_voyage = lines[index + 3] if index + 3 < len(lines) else ""
        event = {
            "Date": line,
            "Location": location,
            "Description": description,
            "Status": "estimated" if "estimated" in description.casefold() else "actual",
        }
        if vessel_voyage and not _looks_like_event_description(vessel_voyage) and not _DATE_RE.fullmatch(vessel_voyage):
            event["VesselVoyage"] = vessel_voyage
        events.append(event)
    return events


def _looks_like_event_description(value: str) -> bool:
    normalized = value.casefold()
    markers = (
        "arrival",
        "arrival",
        "departure",
        "loaded",
        "discharged",
        "transshipment",
        "received at cy",
        "empty to shipper",
        "delivery",
        "gate",
    )
    return any(marker in normalized for marker in markers)


def _final_eta_vessel(events: list[dict[str, str]]) -> str | None:
    for event in events:
        if "estimated time of arrival" in event["Description"].casefold():
            return event.get("VesselVoyage")
    return None


def _split_vessel_voyage(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = value.rsplit(maxsplit=1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]
