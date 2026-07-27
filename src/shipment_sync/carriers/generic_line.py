from __future__ import annotations

from datetime import datetime
import os
import re
from typing import Any
from urllib.parse import quote

import requests

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.common import (
    bounded_response_text,
    extract_container_numbers,
    extract_event_state_hint,
    extract_eta_time,
    extract_first,
    extract_json_from_http_response,
    get_with_retries,
    parse_event_time,
    to_dcsa_movement_name,
)
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus


class GenericLineAdapter(CarrierAdapter):
    """Config-driven adapter for carriers without dedicated integrations yet."""

    def __init__(
        self,
        *,
        env_prefix: str,
        line_label: str,
        default_page_url_template: str,
        challenge_markers: tuple[str, ...] = (),
    ) -> None:
        self.env_prefix = env_prefix
        self.line_label = line_label
        self.default_page_url_template = default_page_url_template
        self.challenge_markers = tuple(m.lower() for m in challenge_markers if m)

        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.url_template = os.getenv(f"{env_prefix}_TRACKING_URL_TEMPLATE", default_page_url_template).strip()
        self.api_url = os.getenv(f"{env_prefix}_TRACKING_API_URL", "").strip()
        self.api_key = os.getenv(f"{env_prefix}_API_KEY", "").strip()
        self.api_key_header = os.getenv(f"{env_prefix}_API_KEY_HEADER", "X-API-Key").strip() or "X-API-Key"
        self.ref_param = os.getenv(f"{env_prefix}_REF_PARAM", "reference").strip() or "reference"
        self.type_param = os.getenv(f"{env_prefix}_TYPE_PARAM", "referenceType").strip() or "referenceType"
        self.booking_type_code = os.getenv(f"{env_prefix}_BOOKING_TYPE_CODE", "booking").strip() or "booking"
        self.container_type_code = os.getenv(f"{env_prefix}_CONTAINER_TYPE_CODE", "container").strip() or "container"
        self.timeout_seconds = int(os.getenv(f"{env_prefix}_TIMEOUT_SECONDS", "45"))
        self.max_retries = int(os.getenv(f"{env_prefix}_MAX_RETRIES", "2"))
        self.retry_delay_seconds = float(os.getenv(f"{env_prefix}_RETRY_DELAY_SECONDS", "2"))
        self.page_link_template = os.getenv(
            f"{env_prefix}_TRACKING_PAGE_URL_TEMPLATE",
            self.default_page_url_template,
        ).strip() or self.default_page_url_template
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }
        )

    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        reference, ref_type_code = _pick_reference(
            shipment=shipment,
            booking_code=self.booking_type_code,
            container_code=self.container_type_code,
        )
        source_url = _build_source_url(self.page_link_template, reference, ref_type_code)
        payload, source = self._fetch_payload(reference=reference, ref_type_code=ref_type_code)
        discovered_containers = extract_container_numbers(payload)

        recent_moves = _extract_moves(payload)
        latest_move = recent_moves[0] if recent_moves else None
        eta_time = extract_eta_time(payload)
        eta_local_text = _extract_eta_raw(payload)

        if self.eta_only_mode:
            return ShipmentStatus(
                status_text=_eta_status_text(eta_time),
                eta_time=eta_time,
                eta_local_text=eta_local_text,
                latest_move=latest_move,
                recent_moves=recent_moves,
                discovered_containers=discovered_containers,
                raw_source=source,
                source_url=source_url,
            )

        status_text = (
            (latest_move.name if latest_move else None)
            or extract_first(payload, ["status", "shipmentStatus", "transportStatus", "latestEvent", "eventDescription"])
            or _eta_status_text(eta_time)
        )
        location = (
            (latest_move.location if latest_move else None)
            or extract_first(payload, ["location", "locationName", "city", "port", "eventLocation", "nodeName", "unLocCode"])
        )
        event_time = (latest_move.event_time if latest_move else None) or parse_event_time(
            extract_first(payload, ["eventTime", "eventDateTime", "actualTime", "timestamp", "dateTime", "date", "eventDate"])
        )
        movement_details = extract_first(
            payload,
            ["eventDescription", "milestoneName", "nodeName", "transportStatus", "cargoStatus", "eventType"],
        )

        return ShipmentStatus(
            status_text=status_text,
            location=location,
            event_time=event_time,
            eta_time=eta_time,
            eta_local_text=eta_local_text,
            latest_move=latest_move,
            recent_moves=recent_moves,
            discovered_containers=discovered_containers,
            raw_source=source,
            source_url=source_url,
            movement_details=movement_details,
        )

    def _fetch_payload(self, *, reference: str, ref_type_code: str) -> tuple[dict[str, Any], str]:
        headers = {self.api_key_header: self.api_key} if self.api_key else {}

        if self.api_url:
            response = get_with_retries(
                self.session,
                self.api_url,
                params={self.ref_param: reference, self.type_param: ref_type_code},
                headers=headers,
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            payload = self._parse_payload(response)
            return payload, f"{self.env_prefix.lower()}-api:{self.api_url}"

        if self.url_template:
            if "{reference}" in self.url_template or "{type}" in self.url_template:
                url = self.url_template.format(reference=quote(reference), type=quote(ref_type_code))
                response = get_with_retries(
                    self.session,
                    url,
                    headers=headers,
                    timeout_seconds=self.timeout_seconds,
                    max_retries=self.max_retries,
                    retry_delay_seconds=self.retry_delay_seconds,
                )
            else:
                response = get_with_retries(
                    self.session,
                    self.url_template,
                    params={self.ref_param: reference, self.type_param: ref_type_code},
                    headers=headers,
                    timeout_seconds=self.timeout_seconds,
                    max_retries=self.max_retries,
                    retry_delay_seconds=self.retry_delay_seconds,
                )
                url = response.url
            payload = self._parse_payload(response)
            return payload, f"{self.env_prefix.lower()}-web:{url}"

        raise ValueError(
            f"adapter not configured: set {self.env_prefix}_TRACKING_URL_TEMPLATE or {self.env_prefix}_TRACKING_API_URL"
        )

    def _parse_payload(self, response: requests.Response) -> dict[str, Any]:
        body = bounded_response_text(response)
        normalized = body.lower()
        if self._looks_blocked(normalized):
            raise ValueError(f"{self.line_label} endpoint blocked by anti-bot challenge")
        if response.status_code >= 400:
            snippet = body.strip().replace("\n", " ")[:240]
            raise ValueError(f"{self.line_label} request failed HTTP {response.status_code}: {snippet}")
        try:
            payload = extract_json_from_http_response(response)
            if isinstance(payload, dict):
                return payload
            return {"data": payload}
        except Exception:
            snippet = body.strip().replace("\n", " ")[:240]
            raise ValueError(f"Could not parse JSON payload from {self.line_label} tracking response: {snippet}")

    def _looks_blocked(self, normalized_body: str) -> bool:
        if not normalized_body:
            return False
        for marker in self.challenge_markers:
            if marker in normalized_body:
                return True
        return False


def _pick_reference(shipment: ShipmentRef, booking_code: str, container_code: str) -> tuple[str, str]:
    if shipment.container_no:
        return _normalize_reference(shipment.container_no), container_code
    if shipment.booking_no:
        return _normalize_reference(shipment.booking_no), booking_code
    raise ValueError("Missing booking/container number")


def _normalize_reference(reference: str) -> str:
    cleaned = reference.strip()
    if not cleaned:
        return cleaned
    tokens = [tok.strip() for tok in re.split(r"[,\s]+", cleaned) if tok.strip()]
    if not tokens:
        return cleaned
    for token in tokens:
        if re.match(r"^[A-Za-z]{4}\d{7}$", token):
            return token
    return tokens[0]


def _extract_moves(payload: dict[str, Any]) -> list[MovementEvent]:
    events = _extract_event_list(payload)
    moves: list[MovementEvent] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        local_time_text = extract_first(
            event,
            [
                "eventLocalPortDate",
                "eventDateTime",
                "eventTime",
                "actualTime",
                "timestamp",
                "dateTime",
                "eventDate",
                "date",
            ],
        )
        state_hint = extract_event_state_hint(event)
        raw_name = extract_first(
            event,
            [
                "eventName",
                "eventDescription",
                "milestoneName",
                "status",
                "movement",
                "description",
                "eventType",
                "transportEventTypeCode",
                "shipmentEventTypeCode",
                "equipmentEventTypeCode",
            ],
        )
        moves.append(
            MovementEvent(
                name=to_dcsa_movement_name(event=event, fallback_name=raw_name),
                location=extract_first(
                    event,
                    ["locationName", "location", "city", "port", "eventLocation", "nodeName", "unLocCode"],
                ),
                event_time=parse_event_time(local_time_text),
                event_time_local_text=local_time_text,
                event_state=_normalize_event_state(state_hint),
            )
        )

    return sorted(
        moves,
        key=lambda m: (m.event_time is not None, m.event_time.isoformat() if m.event_time else ""),
        reverse=True,
    )


def _extract_event_list(payload: dict[str, Any]) -> list[Any]:
    direct_candidates = [
        "events",
        "eventList",
        "movements",
        "movementEvents",
        "trackingEvents",
        "milestones",
        "history",
        "activities",
        "journey",
        "transportEvents",
        "shipmentEvents",
        "equipmentEvents",
        "data",
        "list",
        "items",
    ]
    for key in direct_candidates:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested_key in ("events", "list", "items", "data"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return nested
    return []


def _extract_eta_raw(payload: dict[str, Any]) -> str | None:
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


def _normalize_event_state(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"actual", "act", "completed", "complete", "confirmed", "a"}:
        return "actual"
    if normalized in {"estimated", "estimate", "est", "planned", "plan", "forecast", "expected", "scheduled", "e"}:
        return "estimated"
    return normalized


def _eta_status_text(eta: datetime | None) -> str:
    if eta is None:
        return "ETA unavailable"
    return f"ETA {eta.isoformat()}"


def _build_source_url(template: str, reference: str, ref_type_code: str) -> str:
    cleaned = template.strip() if template else ""
    if not cleaned:
        return ""
    if "{reference}" in cleaned or "{type}" in cleaned:
        return cleaned.format(reference=quote(reference), type=quote(ref_type_code))
    sep = "&" if "?" in cleaned else "?"
    return f"{cleaned}{sep}reference={quote(reference)}&type={quote(ref_type_code)}"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
