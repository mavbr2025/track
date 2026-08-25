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


@dataclass(frozen=True)
class MscBrowserCapture:
    """A validated operator-copied result linked to one ClickUp task."""

    task_id: str
    capture: str


@dataclass(frozen=True)
class MscBrowserFailure:
    """A carrier outcome that must be visible to the shipment owner."""

    task_id: str
    reference: str
    error: str


def build_queue(shipments: Iterable[ShipmentRef]) -> list[MscBrowserQueueItem]:
    """Build a local review queue. This function performs no carrier or ClickUp writes."""
    items: list[MscBrowserQueueItem] = []
    for shipment in shipments:
        if not is_msc_line(shipment.shipping_line):
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


def read_import_batch(path: Path) -> tuple[list[MscBrowserCapture], list[MscBrowserFailure]]:
    """Load a local MSC browser-review batch without making any external write.

    Captures must be copied from the normal public MSC result page. Failures are
    separate from captures so a missing carrier result can never be projected as
    a shipment status or a Last T&T Update.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("MSC import batch must be a JSON object")

    captures = [_capture_from_dict(item) for item in _list_value(payload, "captures")]
    failures = [_failure_from_dict(item) for item in _list_value(payload, "failures")]
    if not captures and not failures:
        raise ValueError("MSC import batch must include at least one capture or failure")

    capture_ids = [item.task_id for item in captures]
    duplicate_captures = sorted({task_id for task_id in capture_ids if capture_ids.count(task_id) > 1})
    if duplicate_captures:
        raise ValueError(f"MSC import batch includes duplicate capture task IDs: {', '.join(duplicate_captures)}")
    failure_ids = [item.task_id for item in failures]
    duplicate_failures = sorted({task_id for task_id in failure_ids if failure_ids.count(task_id) > 1})
    if duplicate_failures:
        raise ValueError(f"MSC import batch includes duplicate failure task IDs: {', '.join(duplicate_failures)}")
    return captures, failures


def status_from_browser_capture(text: str) -> ShipmentStatus:
    """Parse an operator-copied MSC result into the existing MSC projection model.

    The parser accepts a visible result with a container identifier and dated
    tracking events. A POD ETA is optional because MSC removes it from some
    completed shipments; the shared MSC projection then derives the relevant
    date from the actual movement history, as the ECS adapter already does.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    containers = _container_references(text)
    if not containers:
        raise ValueError("MSC browser capture does not contain a container number")

    eta = _value_after_label(lines, "POD ETA")
    if eta and not _DATE_RE.fullmatch(eta):
        raise ValueError("MSC browser capture contains an invalid POD ETA")

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


def _list_value(payload: dict[str, object], key: str) -> list[object]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"MSC import batch field {key!r} must be a list")
    return value


def _capture_from_dict(value: object) -> MscBrowserCapture:
    if not isinstance(value, dict):
        raise ValueError("MSC capture entries must be objects")
    task_id = str(value.get("task_id") or "").strip()
    capture = value.get("capture")
    if not task_id or not isinstance(capture, str) or not capture.strip():
        raise ValueError("MSC capture entries require task_id and non-empty capture")
    return MscBrowserCapture(task_id=task_id, capture=capture)


def _failure_from_dict(value: object) -> MscBrowserFailure:
    if not isinstance(value, dict):
        raise ValueError("MSC failure entries must be objects")
    task_id = str(value.get("task_id") or "").strip()
    reference = str(value.get("reference") or "").strip()
    error = str(value.get("error") or "").strip()
    if not task_id or not reference or not error:
        raise ValueError("MSC failure entries require task_id, reference, and error")
    return MscBrowserFailure(task_id=task_id, reference=reference, error=error)


def is_msc_line(shipping_line: str) -> bool:
    return shipping_line.strip().lower() in {"msc", "msc shipping line", "mediterranean shipping company"}


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
