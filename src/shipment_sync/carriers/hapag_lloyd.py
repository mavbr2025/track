import os
import re
from datetime import datetime, timedelta, timezone
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


class HapagLloydAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.url_template = os.getenv("HAPAG_TRACKING_URL_TEMPLATE", "").strip()
        self.api_url = os.getenv("HAPAG_TRACKING_API_URL", "https://api.hlag.com/hlag/external/v2/events/").strip()
        self.api_key = os.getenv("HAPAG_API_KEY", "").strip()
        self.api_key_header = os.getenv("HAPAG_API_KEY_HEADER", "X-API-Key").strip()
        self.client_id = os.getenv("HAPAG_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("HAPAG_CLIENT_SECRET", "").strip()
        self.client_id_header = os.getenv("HAPAG_CLIENT_ID_HEADER", "X-IBM-Client-Id").strip()
        self.client_secret_header = os.getenv("HAPAG_CLIENT_SECRET_HEADER", "X-IBM-Client-Secret").strip()
        self.ref_param = os.getenv("HAPAG_REF_PARAM", "reference").strip()
        self.type_param = os.getenv("HAPAG_TYPE_PARAM", "referenceType").strip()
        self.equipment_ref_param = os.getenv("HAPAG_EQUIPMENT_REF_PARAM", "equipmentReference").strip()
        self.booking_ref_param = os.getenv("HAPAG_BOOKING_REF_PARAM", "carrierBookingReference").strip()
        self.transport_document_ref_param = os.getenv("HAPAG_TRANSPORT_DOCUMENT_REF_PARAM", "transportDocumentReference").strip()
        self.booking_type_code = os.getenv("HAPAG_BOOKING_TYPE_CODE", "booking").strip()
        self.container_type_code = os.getenv("HAPAG_CONTAINER_TYPE_CODE", "container").strip()
        self.page_url_template = os.getenv(
            "HAPAG_TRACKING_PAGE_URL_TEMPLATE",
            "https://www.hapag-lloyd.com/en/online-business/track/track-by-container-solution.html?container={reference}",
        ).strip()
        self.bearer_token = os.getenv("HAPAG_BEARER_TOKEN", "").strip()
        self.oauth_token_url = os.getenv("HAPAG_OAUTH_TOKEN_URL", "").strip()
        self.oauth_client_id = os.getenv("HAPAG_OAUTH_CLIENT_ID", "").strip()
        self.oauth_client_secret = os.getenv("HAPAG_OAUTH_CLIENT_SECRET", "").strip()
        self.oauth_scope = os.getenv("HAPAG_OAUTH_SCOPE", "").strip()
        self.timeout_seconds = int(os.getenv("HAPAG_TIMEOUT_SECONDS", "60"))
        self.max_retries = int(os.getenv("HAPAG_MAX_RETRIES", "2"))
        self.retry_delay_seconds = float(os.getenv("HAPAG_RETRY_DELAY_SECONDS", "2"))
        self.session = requests.Session()
        self._oauth_access_token: str | None = None
        self._oauth_expires_at: datetime | None = None

    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        reference, ref_type_code = _pick_reference(shipment, self.booking_type_code, self.container_type_code)
        reference = _normalize_reference(reference)
        source_url = _build_source_url(self.page_url_template, reference, ref_type_code)
        payload, source = self._fetch_payload(reference, ref_type_code)

        eta_time = extract_eta_time(payload)
        eta_local_text = _extract_eta_raw(payload)
        recent_moves = _extract_moves(payload)

        if self.eta_only_mode:
            return ShipmentStatus(
                status_text=_eta_status_text(eta_time),
                eta_time=eta_time,
                eta_local_text=eta_local_text,
                latest_move=recent_moves[0] if recent_moves else None,
                recent_moves=recent_moves,
                raw_source=source,
                source_url=source_url,
            )

        latest_move = recent_moves[0] if recent_moves else None
        status_text = (
            (latest_move.name if latest_move else None)
            or extract_first(
                payload,
                [
                    "status",
                    "shipmentStatus",
                    "cargoStatus",
                    "latestEvent",
                    "eventDescription",
                    "milestoneName",
                ],
            )
            or _eta_status_text(eta_time)
        )
        location = (
            (latest_move.location if latest_move else None)
            or extract_first(payload, ["location", "locationName", "city", "port", "eventLocation", "nodeName"])
        )
        event_time = (latest_move.event_time if latest_move else None) or parse_event_time(
            extract_first(payload, ["eventTime", "eventDateTime", "actualTime", "timestamp", "dateTime", "date", "eventDate"])
        )
        movement_details = extract_first(
            payload,
            ["eventDescription", "milestoneName", "nodeName", "transportStatus", "cargoStatus"],
        )

        return ShipmentStatus(
            status_text=status_text,
            location=location,
            event_time=event_time,
            eta_time=eta_time,
            eta_local_text=eta_local_text,
            latest_move=latest_move,
            recent_moves=recent_moves,
            raw_source=source,
            source_url=source_url,
            movement_details=movement_details,
        )

    def _fetch_payload(self, reference: str, ref_type_code: str) -> tuple[dict[str, Any], str]:
        headers = self._base_headers()
        params = self._build_params(reference, ref_type_code)

        if self.url_template:
            url = self.url_template.format(reference=reference, type=ref_type_code)
            response = get_with_retries(
                self.session,
                url,
                params=params,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            payload = extract_json_from_http_response(response)
            return payload, f"hapag-web:{response.url}"

        if not self.api_url:
            raise ValueError("Set HAPAG_TRACKING_URL_TEMPLATE or HAPAG_TRACKING_API_URL")

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
        return payload, f"hapag-api:{self.api_url}"

    def _build_params(self, reference: str, ref_type_code: str) -> dict[str, str]:
        code = ref_type_code.strip().lower()
        params: dict[str, str] = {}

        if code in {"container", "equipment", "cntr", "c"}:
            params[self.equipment_ref_param] = reference
            return params
        if code in {"booking", "bkg", "b"}:
            params[self.booking_ref_param] = reference
            return params
        if code in {"transport_document", "transport-document", "td", "bl", "bol"}:
            params[self.transport_document_ref_param] = reference
            return params

        # Fallback for non-standard configurations.
        params[self.ref_param] = reference
        params[self.type_param] = ref_type_code
        return params

    def _base_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        if self.client_id:
            headers[self.client_id_header] = self.client_id
        if self.client_secret:
            headers[self.client_secret_header] = self.client_secret
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
            return headers
        if self.oauth_token_url and self.oauth_client_id and self.oauth_client_secret:
            headers["Authorization"] = f"Bearer {self._get_oauth_access_token()}"
        return headers

    def _get_oauth_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._oauth_access_token and self._oauth_expires_at and now < self._oauth_expires_at:
            return self._oauth_access_token

        data = {"grant_type": "client_credentials"}
        if self.oauth_scope:
            data["scope"] = self.oauth_scope

        attempts = [
            {
                "auth": (self.oauth_client_id, self.oauth_client_secret),
                "data": dict(data),
            },
            {
                "auth": None,
                "data": {
                    **data,
                    "client_id": self.oauth_client_id,
                    "client_secret": self.oauth_client_secret,
                },
            },
        ]
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        last_error: Exception | None = None
        for attempt in attempts:
            try:
                response = self.session.post(
                    self.oauth_token_url,
                    auth=attempt["auth"],
                    data=attempt["data"],
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                token = payload.get("access_token")
                if isinstance(token, str) and token:
                    expires_in = payload.get("expires_in", 300)
                    try:
                        ttl_seconds = int(expires_in)
                    except Exception:
                        ttl_seconds = 300
                    self._oauth_access_token = token
                    self._oauth_expires_at = now + timedelta(seconds=max(60, ttl_seconds - 30))
                    return token
            except Exception as exc:
                last_error = exc

        if last_error:
            raise ValueError(f"Hapag OAuth token request failed: {last_error}")
        raise ValueError("Hapag OAuth token response missing access_token")


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
            ],
        )
        location = extract_first(
            event,
            ["locationName", "location", "city", "port", "eventLocation", "nodeName", "unLocCode"],
        )
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
        state_hint = extract_first(event, ["eventClassifierCode", "trigger", "eventType", "status"])
        moves.append(
            MovementEvent(
                name=to_dcsa_movement_name(event=event, fallback_name=raw_name),
                location=location,
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
        "movements",
        "movementEvents",
        "trackingEvents",
        "milestones",
        "history",
        "activities",
        "journey",
    ]
    for key in direct_candidates:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = value.get("events")
            if isinstance(nested, list):
                return nested

    data = payload.get("data")
    if isinstance(data, dict):
        for key in direct_candidates:
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = value.get("events")
                if isinstance(nested, list):
                    return nested
    elif isinstance(data, list):
        return data

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
    if normalized in {"actual", "act", "completed", "complete", "confirmed"}:
        return "actual"
    if normalized in {"estimated", "estimate", "est", "planned", "plan", "forecast", "expected", "scheduled"}:
        return "estimated"
    return normalized


def _eta_status_text(eta: datetime | None) -> str:
    if eta is None:
        return "ETA unavailable"
    return f"ETA {eta.isoformat()}"


def _build_source_url(template: str, reference: str, ref_type_code: str) -> str:
    cleaned = template.strip()
    if not cleaned:
        cleaned = "https://www.hapag-lloyd.com/en/online-business/track/track-by-container-solution.html"
    if "{reference}" in cleaned or "{type}" in cleaned:
        return cleaned.format(reference=quote(reference), type=quote(ref_type_code))
    sep = "&" if "?" in cleaned else "?"
    return f"{cleaned}{sep}reference={quote(reference)}&type={quote(ref_type_code)}"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
