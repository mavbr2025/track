import os
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.common import (
    extract_eta_time,
    extract_first,
    extract_json_from_http_response,
    get_with_retries,
    parse_event_time,
    to_dcsa_movement_name,
)
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus


class OneAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.use_edh_api = _env_bool("ONE_USE_EDH_API", default=True)
        self.edh_base_url = os.getenv("ONE_EDH_BASE_URL", "https://ecomm.one-line.com/api/v1/edh").strip().rstrip("/")
        self.url_template = os.getenv("ONE_TRACKING_URL_TEMPLATE", "").strip()
        self.api_url = os.getenv("ONE_TRACKING_API_URL", "").strip()
        self.api_key = os.getenv("ONE_API_KEY", "").strip()
        self.api_key_header = os.getenv("ONE_API_KEY_HEADER", "X-API-Key")
        self.ref_param = os.getenv("ONE_REF_PARAM", "trakNoParam")
        self.type_param = os.getenv("ONE_TYPE_PARAM", "trakNoTpCdParam")
        self.booking_type_code = os.getenv("ONE_BOOKING_TYPE_CODE", "B")
        self.container_type_code = os.getenv("ONE_CONTAINER_TYPE_CODE", "C")
        self.timeout_seconds = int(os.getenv("ONE_TIMEOUT_SECONDS", "60"))
        self.max_retries = int(os.getenv("ONE_MAX_RETRIES", "2"))
        self.retry_delay_seconds = float(os.getenv("ONE_RETRY_DELAY_SECONDS", "2"))
        self.session = requests.Session()

    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        reference, ref_type_code = _pick_reference(shipment, self.booking_type_code, self.container_type_code)
        reference = _normalize_reference(reference)
        source_url = _build_one_tracking_url(reference, ref_type_code, self.container_type_code)

        if self.use_edh_api:
            edh_status = self._fetch_status_from_edh(reference, ref_type_code)
            if edh_status is not None:
                return edh_status

        payload, source = self._fetch_payload(reference, ref_type_code)
        eta_time = extract_eta_time(payload)
        eta_local_text = _extract_eta_raw(payload)

        if self.eta_only_mode:
            return ShipmentStatus(
                status_text=_eta_status_text(eta_time),
                eta_time=eta_time,
                eta_local_text=eta_local_text,
                raw_source=source,
                source_url=source_url,
            )

        status_text = extract_first(
            payload,
            [
                "status",
                "cargoStatus",
                "shipmentStatus",
                "latestEvent",
                "eventDescription",
                "milestoneName",
            ],
        ) or "Unknown"
        location = extract_first(
            payload,
            ["location", "locationName", "city", "port", "eventLocation", "nodeName"],
        )
        event_time_raw = extract_first(
            payload,
            ["eventTime", "eventDateTime", "actualTime", "timestamp", "dateTime", "date", "eventDate"],
        )

        movement_details = extract_first(
            payload,
            ["eventDescription", "milestoneName", "nodeName", "transportStatus", "cargoStatus"],
        )

        return ShipmentStatus(
            status_text=status_text,
            location=location,
            event_time=parse_event_time(event_time_raw),
            eta_time=eta_time,
            eta_local_text=eta_local_text,
            raw_source=source,
            source_url=source_url,
            movement_details=movement_details,
        )

    def _fetch_status_from_edh(self, reference: str, ref_type_code: str) -> ShipmentStatus | None:
        source_url = _build_one_tracking_url(reference, ref_type_code, self.container_type_code)
        search_type = _to_one_search_type(ref_type_code, self.container_type_code)
        search_url = f"{self.edh_base_url}/containers/track-and-trace/search"
        payload = {
            "page": 1,
            "page_length": 10,
            "filters": {
                "search_text": reference,
                "search_type": search_type,
            },
            "timestamp": int(time.time() * 1000),
        }

        try:
            response = self.session.post(search_url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            search_result = response.json()
        except Exception:
            return None

        if not isinstance(search_result, dict):
            return None
        data = search_result.get("data")
        if not isinstance(data, list) or not data:
            return None

        first_item = data[0] if isinstance(data[0], dict) else None
        if not first_item:
            return None

        eta_time, eta_local_text = _extract_eta_from_search_item(first_item)
        raw_source = f"one-edh-search:{search_url}"
        booking_no = _safe_text(first_item.get("bookingNo"))
        container_no = _safe_text(first_item.get("containerNo"))

        voyage_legs = self._fetch_voyage_list(booking_no) if booking_no else []
        if eta_time is None and voyage_legs:
            eta_from_voyage, eta_from_voyage_local = _extract_eta_from_voyage_list_data(voyage_legs)
            if eta_from_voyage is not None:
                eta_time = eta_from_voyage
                eta_local_text = eta_from_voyage_local
                raw_source = f"one-edh-voyage:{self.edh_base_url}/vessel/track-and-trace/voyage-list"

        recent_moves = self._fetch_recent_moves(booking_no, container_no)
        if not recent_moves and voyage_legs:
            departure_move = _extract_departure_move_from_voyage_list_data(voyage_legs)
            if departure_move is not None:
                recent_moves = [departure_move]
        latest_move = recent_moves[0] if recent_moves else _latest_move_from_search_item(first_item)

        if self.eta_only_mode:
            return ShipmentStatus(
                status_text=_eta_status_text(eta_time),
                eta_time=eta_time,
                eta_local_text=eta_local_text,
                latest_move=latest_move,
                recent_moves=recent_moves,
                raw_source=raw_source,
                source_url=source_url,
            )

        latest_event = first_item.get("latestEvent") if isinstance(first_item.get("latestEvent"), dict) else {}
        latest_event_name = _safe_text(latest_event.get("eventName"))
        latest_event_location = _safe_text(latest_event.get("locationName"))
        latest_event_time = parse_event_time(_safe_text(latest_event.get("date")))

        vessel_voyage = first_item.get("vesselVoyage") if isinstance(first_item.get("vesselVoyage"), dict) else {}
        vessel_name = _safe_text(vessel_voyage.get("vesselName"))
        voyage_no = _safe_text(vessel_voyage.get("voyageNo"))
        movement_details = " ".join([x for x in [vessel_name, voyage_no] if x]) or None

        status_text = latest_event_name or _eta_status_text(eta_time)
        return ShipmentStatus(
            status_text=status_text,
            location=latest_event_location,
            event_time=latest_event_time,
            eta_time=eta_time,
            eta_local_text=eta_local_text,
            latest_move=latest_move,
            recent_moves=recent_moves,
            raw_source=raw_source,
            source_url=source_url,
            movement_details=movement_details,
        )

    def _fetch_voyage_list(self, booking_no: str) -> list[dict[str, Any]]:
        url = f"{self.edh_base_url}/vessel/track-and-trace/voyage-list"
        try:
            response = self.session.get(
                url,
                params={"booking_no": booking_no},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []

        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return []
        return [item for item in data if isinstance(item, dict)]

    def _fetch_recent_moves(self, booking_no: str | None, container_no: str | None) -> list[MovementEvent]:
        if not booking_no or not container_no:
            return []
        url = f"{self.edh_base_url}/containers/track-and-trace/cop-events"
        try:
            response = self.session.get(
                url,
                params={"booking_no": booking_no, "container_no": container_no},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []

        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []

        moves: list[MovementEvent] = []
        for event in data:
            if not isinstance(event, dict):
                continue
            location_obj = event.get("location") if isinstance(event.get("location"), dict) else {}
            location = _safe_text(location_obj.get("locationName"))
            event_name = _safe_text(event.get("eventName")) or "Unknown move"
            local_time_text = _safe_text(event.get("eventLocalPortDate")) or _safe_text(event.get("eventDate"))
            event_state = _normalize_event_state(_one_trigger_text(event))
            moves.append(
                MovementEvent(
                    name=to_dcsa_movement_name(event=event, fallback_name=event_name),
                    location=location,
                    event_time=parse_event_time(local_time_text),
                    event_time_local_text=local_time_text,
                    event_state=event_state,
                )
            )

        return sorted(
            moves,
            key=lambda m: (m.event_time is not None, m.event_time.isoformat() if m.event_time else ""),
            reverse=True,
        )

    def _fetch_payload(self, reference: str, ref_type_code: str) -> tuple[dict, str]:
        headers = {self.api_key_header: self.api_key} if self.api_key else {}
        params = {
            self.ref_param: reference,
            self.type_param: ref_type_code,
        }

        if self.url_template:
            response = get_with_retries(
                self.session,
                self.url_template,
                params=params,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            payload = extract_json_from_http_response(response)
            return payload, f"one-web:{response.url}"

        if not self.api_url:
            raise ValueError("Set ONE_TRACKING_URL_TEMPLATE or ONE_TRACKING_API_URL")

        response = get_with_retries(
            self.session,
            self.api_url,
            params=params,
            headers=headers,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            retry_delay_seconds=self.retry_delay_seconds,
        )
        payload = extract_json_from_http_response(response)
        return payload, f"one-api:{self.api_url}"


def _pick_reference(shipment: ShipmentRef, booking_code: str, container_code: str) -> tuple[str, str]:
    if shipment.container_no:
        return shipment.container_no, container_code
    if shipment.booking_no:
        return shipment.booking_no, booking_code
    raise ValueError("Missing booking/container number")


def _normalize_reference(reference: str) -> str:
    cleaned = reference.strip()
    if not cleaned:
        return cleaned
    tokens = [tok.strip() for tok in re.split(r"[,\s]+", cleaned) if tok.strip()]
    if not tokens:
        return cleaned
    # Prefer the first token that looks like a container number.
    for token in tokens:
        if re.match(r"^[A-Za-z]{4}\d{7}$", token):
            return token
    return tokens[0]


def _to_one_search_type(ref_type_code: str, container_type_code: str) -> str:
    if ref_type_code.strip().upper() == container_type_code.strip().upper():
        return "CNTR_NO"
    return "BKG_NO"


def _extract_eta_from_search_item(item: dict[str, Any]) -> tuple[datetime | None, str | None]:
    cargo_events = item.get("cargoEvents")
    pod = item.get("pod") if isinstance(item.get("pod"), dict) else {}
    pod_name = _safe_text(pod.get("locationName"))

    if isinstance(cargo_events, list):
        # Known ETA-related ONE matrix IDs observed in track-and-trace payloads.
        eta, eta_raw = _extract_eta_from_cargo_events(cargo_events, {"E089", "E105", "E078"})
        if eta is not None:
            return eta, eta_raw

        # Fallback: estimated events at POD location.
        eta, eta_raw = _extract_eta_from_cargo_events(cargo_events, set(), pod_name=pod_name)
        if eta is not None:
            return eta, eta_raw

    return None, None


def _extract_eta_from_voyage_list_data(items: list[dict[str, Any]]) -> tuple[datetime | None, str | None]:
    eta_candidates: list[tuple[datetime, str | None]] = []
    for item in items:
        pod = item.get("pod")
        if not isinstance(pod, dict):
            continue
        for key in ("arrivalDate", "berthingDate"):
            eta_raw = _safe_text(pod.get(key))
            eta_candidate = parse_event_time(eta_raw)
            if eta_candidate is not None:
                eta_candidates.append((eta_candidate, eta_raw))

    if not eta_candidates:
        return None, None
    return max(eta_candidates, key=lambda x: x[0])


def _extract_departure_move_from_voyage_list_data(items: list[dict[str, Any]]) -> MovementEvent | None:
    departure_candidates: list[tuple[datetime, str | None, str | None]] = []
    for item in items:
        pol = item.get("pol")
        if not isinstance(pol, dict):
            continue
        departure_raw = _safe_text(pol.get("date"))
        departure_time = parse_event_time(departure_raw)
        if departure_time is None:
            continue
        departure_candidates.append(
            (
                departure_time,
                departure_raw,
                _safe_text(pol.get("locationName")),
            )
        )

    if not departure_candidates:
        return None

    departure_time, departure_raw, location = min(departure_candidates, key=lambda x: x[0])
    return MovementEvent(
        name="Transport Departed (DEPA)",
        location=location,
        event_time=departure_time,
        event_time_local_text=departure_raw,
        event_state="estimated",
    )


def _extract_eta_from_cargo_events(
    cargo_events: list[Any],
    matrix_ids: set[str],
    pod_name: str | None = None,
) -> tuple[datetime | None, str | None]:
    eta_candidates: list[tuple[datetime, str | None]] = []
    normalized_pod = pod_name.strip().upper() if isinstance(pod_name, str) and pod_name.strip() else None

    for event in cargo_events:
        if not isinstance(event, dict):
            continue

        matrix_id = _safe_text(event.get("matrixId"))
        trigger_type = _one_trigger_text(event)
        location_name = _safe_text(event.get("locationName"))

        if matrix_ids and (not matrix_id or matrix_id not in matrix_ids):
            continue

        if not matrix_ids:
            if trigger_type != "ESTIMATED":
                continue
            if normalized_pod and location_name and location_name.strip().upper() != normalized_pod:
                continue

        eta_raw = _safe_text(event.get("localPortDate")) or _safe_text(event.get("date"))
        eta_time = parse_event_time(eta_raw)
        if eta_time is not None:
            eta_candidates.append((eta_time, eta_raw))

    if not eta_candidates:
        return None, None
    return min(eta_candidates, key=lambda x: x[0])


def _latest_move_from_search_item(item: dict[str, Any]) -> MovementEvent | None:
    latest = item.get("latestEvent")
    if not isinstance(latest, dict):
        return None
    event_name = _safe_text(latest.get("eventName"))
    location = _safe_text(latest.get("locationName"))
    event_local_text = _safe_text(latest.get("date"))
    if not event_name and not location and not event_local_text:
        return None
    return MovementEvent(
        name=to_dcsa_movement_name(event=latest, fallback_name=event_name or "Latest move"),
        location=location,
        event_time=parse_event_time(event_local_text),
        event_time_local_text=event_local_text,
        event_state=_normalize_event_state(_one_trigger_text(latest)),
    )


def _safe_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _one_trigger_text(event: dict[str, Any]) -> str | None:
    return _safe_text(event.get("triggerType")) or _safe_text(event.get("trigger"))


def _normalize_event_state(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"actual", "act", "completed", "complete", "confirmed"}:
        return "actual"
    if normalized in {"estimated", "estimate", "est", "planned", "plan", "forecast", "expected", "scheduled"}:
        return "estimated"
    return normalized


def _extract_eta_raw(payload: dict) -> str | None:
    return extract_first(
        payload,
        [
            "eta",
            "etaDate",
            "etaTime",
            "etaDateTime",
            "estimatedArrival",
            "estimatedArrivalDate",
            "estimatedArrivalTime",
            "estimatedArrivalDateTime",
            "estimatedTimeOfArrival",
            "arrivalEstimate",
            "arrivalEstimatedTime",
            "arrivalDateEstimated",
            "plannedArrival",
            "plannedArrivalDate",
            "plannedArrivalTime",
            "plannedArrivalDateTime",
            "scheduledArrival",
            "scheduledArrivalDate",
            "scheduledArrivalTime",
            "scheduledArrivalDateTime",
            "vesselEta",
            "destinationEta",
            "podEta",
            "dischargeEta",
        ],
    )


def _eta_status_text(eta: datetime | None) -> str:
    if eta is None:
        return "ETA unavailable"
    return f"ETA {eta.isoformat()}"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_one_tracking_url(reference: str, ref_type_code: str, container_type_code: str) -> str:
    base = "https://ecomm.one-line.com/one-ecom/manage-shipment/cargo-tracking"
    track_type = "C" if ref_type_code.strip().upper() == container_type_code.strip().upper() else "B"
    return f"{base}?trakNoParam={quote(reference)}&trakNoTpCdParam={quote(track_type)}"
