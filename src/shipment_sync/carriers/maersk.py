import os
import re
from datetime import datetime, timedelta, timezone
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


class MaerskAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.api_mode = os.getenv("MAERSK_API_MODE", "auto").strip().lower()
        self.url_template = os.getenv("MAERSK_TRACKING_URL_TEMPLATE", "").strip()
        self.api_url = os.getenv("MAERSK_TRACKING_API_URL", "").strip()
        self.api_key = os.getenv("MAERSK_API_KEY", "").strip()
        self.api_key_header = os.getenv("MAERSK_API_KEY_HEADER", "X-API-Key")
        self.ref_param = os.getenv("MAERSK_REF_PARAM", "reference")
        self.type_param = os.getenv("MAERSK_TYPE_PARAM", "referenceType")
        self.consumer_key = os.getenv("MAERSK_CONSUMER_KEY", "").strip()
        self.bearer_token = os.getenv("MAERSK_BEARER_TOKEN", "").strip()
        self.oauth_token_url = os.getenv("MAERSK_OAUTH_TOKEN_URL", "").strip()
        self.oauth_client_id = os.getenv("MAERSK_OAUTH_CLIENT_ID", "").strip()
        self.oauth_client_secret = os.getenv("MAERSK_OAUTH_CLIENT_SECRET", "").strip()
        self.oauth_scope = os.getenv("MAERSK_OAUTH_SCOPE", "").strip()
        self.api_version = os.getenv("MAERSK_API_VERSION", "1").strip()
        self.events_limit = int(os.getenv("MAERSK_EVENTS_LIMIT", "100"))
        if self.events_limit <= 0:
            self.events_limit = 100
        self.fetch_all_events = _env_bool("MAERSK_FETCH_ALL_EVENTS", default=True)
        self.web_fallback_enabled = _env_bool("MAERSK_WEB_FALLBACK_ON_API_ERROR", default=True)
        self.timeout_seconds = int(os.getenv("MAERSK_TIMEOUT_SECONDS", "60"))
        self.max_retries = int(os.getenv("MAERSK_MAX_RETRIES", "2"))
        self.retry_delay_seconds = float(os.getenv("MAERSK_RETRY_DELAY_SECONDS", "2"))
        self.session = requests.Session()
        self._oauth_access_token: str | None = None
        self._oauth_expires_at: datetime | None = None

    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        reference, ref_type = _pick_reference(shipment)
        source_url = _build_maersk_tracking_url(reference)
        payload, source = self._fetch_payload(reference, ref_type)
        events = _extract_events(payload)
        payload_eta = extract_eta_time(payload)
        payload_eta_raw = _extract_eta_raw(payload)

        if events:
            status = _status_from_events(events, source, source_url=source_url)
            if not status.eta_time:
                status.eta_time = payload_eta
            if not status.eta_local_text:
                status.eta_local_text = payload_eta_raw
            if self.eta_only_mode:
                status.status_text = _eta_status_text(status.eta_time)
            return status

        if self.eta_only_mode:
            return ShipmentStatus(
                status_text=_eta_status_text(payload_eta),
                eta_time=payload_eta,
                eta_local_text=payload_eta_raw,
                raw_source=source,
                source_url=source_url,
            )

        status_text = extract_first(
            payload,
            ["status", "shipmentStatus", "transportStatus", "latestEvent", "eventDescription"],
        ) or "Unknown"
        location = extract_first(payload, ["location", "locationName", "city", "port", "eventLocation"])
        event_time_raw = extract_first(
            payload,
            ["eventTime", "eventDateTime", "actualTime", "timestamp", "dateTime", "date"],
        )

        movement_details = extract_first(
            payload,
            ["latestEvent", "eventDescription", "transportStatus", "shipmentStatus"],
        )

        return ShipmentStatus(
            status_text=status_text,
            location=location,
            event_time=parse_event_time(event_time_raw),
            eta_time=payload_eta,
            eta_local_text=payload_eta_raw,
            raw_source=source,
            source_url=source_url,
            movement_details=movement_details,
        )

    def _fetch_payload(self, reference: str, ref_type: str) -> tuple[dict, str]:
        headers = {self.api_key_header: self.api_key} if self.api_key else {}

        if self.url_template:
            url = self.url_template.format(reference=reference, type=ref_type)
            response = get_with_retries(
                self.session,
                url,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            payload = extract_json_from_http_response(response)
            return payload, f"maersk-web:{url}"

        if not self.api_url:
            fallback_payload, fallback_source = self._try_web_fallback(reference, ref_type, reason="missing_api_url")
            if fallback_payload is not None:
                return fallback_payload, fallback_source
            raise ValueError("Set MAERSK_TRACKING_URL_TEMPLATE or MAERSK_TRACKING_API_URL")

        if self._use_maersk_events_api():
            events_headers = self._build_events_api_headers()
            try:
                if self.fetch_all_events:
                    all_events = self._fetch_all_events(reference, ref_type, events_headers)
                    return {"events": all_events}, f"maersk-events-api:{self.api_url}"

                params = _events_params(reference, ref_type, self.events_limit, cursor="1")
                response = get_with_retries(
                    self.session,
                    self.api_url,
                    params=params,
                    headers=events_headers,
                    timeout_seconds=self.timeout_seconds,
                    max_retries=self.max_retries,
                    retry_delay_seconds=self.retry_delay_seconds,
                )
                payload = response.json()
                if isinstance(payload, list):
                    return {"events": payload}, f"maersk-events-api:{self.api_url}"
                if isinstance(payload, dict):
                    return payload, f"maersk-events-api:{self.api_url}"
                return {"events": []}, f"maersk-events-api:{self.api_url}"
            except requests.RequestException:
                fallback_payload, fallback_source = self._try_web_fallback(reference, ref_type, reason="events_api_error")
                if fallback_payload is not None:
                    return fallback_payload, fallback_source
                raise

        params = {
            self.ref_param: reference,
            self.type_param: ref_type,
        }
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
        return payload, f"maersk-api:{self.api_url}"

    def _try_web_fallback(self, reference: str, ref_type: str, *, reason: str) -> tuple[dict | None, str]:
        if not self.web_fallback_enabled:
            return None, ""
        url_template = self.url_template or "https://www.maersk.com/tracking/{reference}"
        url = url_template.format(reference=reference, type=ref_type)
        response = get_with_retries(
            self.session,
            url,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            retry_delay_seconds=self.retry_delay_seconds,
        )
        payload = extract_json_from_http_response(response)
        return payload, f"maersk-web-fallback:{reason}:{url}"

    def _fetch_all_events(self, reference: str, ref_type: str, headers: dict[str, str]) -> list[dict]:
        all_events: list[dict] = []
        seen_cursors: set[str] = set()
        cursor = "1"

        for _ in range(100):
            if cursor in seen_cursors:
                break
            seen_cursors.add(cursor)

            params = _events_params(reference, ref_type, self.events_limit, cursor=cursor)
            response = get_with_retries(
                self.session,
                self.api_url,
                params=params,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            payload = response.json()
            event_batch = _extract_events(payload if isinstance(payload, dict) else {"events": payload})
            if event_batch:
                all_events.extend(event_batch)

            next_cursor = _extract_next_cursor(payload, cursor=cursor, page_size=self.events_limit, count=len(event_batch))
            if not next_cursor:
                break
            cursor = next_cursor

        return all_events

    def _use_maersk_events_api(self) -> bool:
        if self.api_mode == "events":
            return True
        if self.api_mode == "legacy":
            return False
        return bool(self.consumer_key or self.bearer_token or self.oauth_token_url)

    def _build_events_api_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"API-Version": self.api_version}
        if self.consumer_key:
            headers["Consumer-Key"] = self.consumer_key
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
            return headers
        access_token = self._get_oauth_access_token()
        headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def _get_oauth_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._oauth_access_token and self._oauth_expires_at and now < self._oauth_expires_at:
            return self._oauth_access_token

        token_url = self.oauth_token_url
        client_id = self.oauth_client_id or self.consumer_key
        client_secret = self.oauth_client_secret or self.api_key
        if not token_url or not client_id or not client_secret:
            raise ValueError(
                "For Maersk events API set either MAERSK_BEARER_TOKEN "
                "or MAERSK_OAUTH_TOKEN_URL + MAERSK_OAUTH_CLIENT_ID/SECRET"
            )

        base_data = {"grant_type": "client_credentials"}
        if self.oauth_scope:
            base_data["scope"] = self.oauth_scope

        base_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.consumer_key:
            base_headers["Consumer-Key"] = self.consumer_key

        attempts = [
            {
                "auth": (client_id, client_secret),
                "data": dict(base_data),
                "headers": dict(base_headers),
            },
            {
                "auth": None,
                "data": {
                    **base_data,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                "headers": dict(base_headers),
            },
        ]

        last_error: Exception | None = None
        for attempt in attempts:
            try:
                response = self.session.post(
                    token_url,
                    auth=attempt["auth"],
                    data=attempt["data"],
                    headers=attempt["headers"],
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 400:
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
            raise ValueError(f"Maersk OAuth token request failed: {last_error}")
        raise ValueError("Maersk OAuth token response missing access_token")


def _pick_reference(shipment: ShipmentRef) -> tuple[str, str]:
    if shipment.container_no:
        return _normalize_reference(shipment.container_no), "container"
    if shipment.booking_no:
        return _normalize_reference(shipment.booking_no), "booking"
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


def _events_params(reference: str, ref_type: str, limit: int, *, cursor: str) -> dict[str, str]:
    params: dict[str, str] = {"limit": str(limit), "cursor": cursor}
    if ref_type == "container":
        params["equipmentReference"] = reference
    else:
        params["carrierBookingReference"] = reference
    return params


def _extract_events(payload: dict) -> list[dict]:
    if isinstance(payload.get("events"), list):
        return [e for e in payload["events"] if isinstance(e, dict)]
    if isinstance(payload.get("data"), list):
        return [e for e in payload["data"] if isinstance(e, dict)]
    return []


def _extract_next_cursor(payload: object, *, cursor: str, page_size: int, count: int) -> str | None:
    if isinstance(payload, dict):
        direct = _first_text(payload, ["nextCursor", "next_cursor", "nextPage", "next_page", "cursorNext"])
        if direct:
            return direct

        for parent_key in ("pageInfo", "pagination", "meta"):
            parent = payload.get(parent_key)
            if isinstance(parent, dict):
                nested = _first_text(parent, ["nextCursor", "next_cursor", "nextPage", "next_page"])
                if nested:
                    return nested
                has_next = _first_bool(parent, ["hasNext", "has_next", "hasMore", "has_more"])
                if has_next:
                    return _increment_cursor(cursor)

        has_next_payload = _first_bool(payload, ["hasNext", "has_next", "hasMore", "has_more"])
        if has_next_payload:
            return _increment_cursor(cursor)

    if count >= page_size:
        return _increment_cursor(cursor)
    return None


def _status_from_events(events: list[dict], source: str, *, source_url: str | None) -> ShipmentStatus:
    ordered = sorted(events, key=_event_sort_key, reverse=True)
    latest = ordered[0]
    latest_move = _event_to_movement(latest)
    status_text = latest_move.name if latest_move and latest_move.name else "Unknown"
    location = extract_first(
        latest,
        ["locationName", "UNLocationCode", "facilityCode", "eventLocation", "city", "port"],
    )
    event_time_raw = extract_first(latest, ["eventDateTime", "eventCreatedDateTime", "dateTime", "timestamp"])
    event_time = parse_event_time(event_time_raw)
    event_id = extract_first(latest, ["eventID"])
    raw_source = source if not event_id else f"{source}:{event_id}"
    event_type = extract_first(latest, ["eventType"])
    classifier = extract_first(latest, ["eventClassifierCode"])
    movement_code = (
        extract_first(latest, ["equipmentEventTypeCode"])
        or extract_first(latest, ["shipmentEventTypeCode"])
        or extract_first(latest, ["transportEventTypeCode"])
    )
    detail_parts = [x for x in [event_type, classifier, movement_code] if x]
    movement_details = " / ".join(detail_parts) if detail_parts else None
    recent_moves = [_event_to_movement(event) for event in ordered]
    return ShipmentStatus(
        status_text=status_text,
        location=location,
        event_time=event_time,
        eta_time=extract_eta_time(latest),
        eta_local_text=_extract_eta_raw(latest),
        latest_move=latest_move,
        recent_moves=recent_moves,
        raw_source=raw_source,
        source_url=source_url,
        movement_details=movement_details,
    )


def _event_sort_key(event: dict) -> tuple[int, str]:
    raw = (
        extract_first(event, ["eventDateTime"])
        or extract_first(event, ["eventCreatedDateTime"])
        or ""
    )
    dt = parse_event_time(raw)
    if dt:
        return (1, dt.isoformat())
    return (0, raw)


def _event_to_movement(event: dict) -> MovementEvent:
    raw_name = extract_first(
        event,
        [
            "eventDescription",
            "shipmentEventTypeCode",
            "equipmentEventTypeCode",
            "transportEventTypeCode",
            "eventType",
        ],
    )
    name = to_dcsa_movement_name(event=event, fallback_name=raw_name)
    location = extract_first(
        event,
        ["locationName", "UNLocationCode", "facilityCode", "eventLocation", "city", "port"],
    )
    local_time_text = (
        extract_first(event, ["eventDateTime"])
        or extract_first(event, ["eventCreatedDateTime"])
        or extract_first(event, ["dateTime"])
        or extract_first(event, ["timestamp"])
    )
    classifier = extract_first(event, ["eventClassifierCode", "eventType"])
    return MovementEvent(
        name=name,
        location=location,
        event_time=parse_event_time(local_time_text),
        event_time_local_text=local_time_text,
        event_state=_normalize_event_state(classifier),
    )


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


def _first_text(payload: dict, keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def _first_bool(payload: dict, keys: list[str]) -> bool | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "y", "1"}:
                return True
            if normalized in {"false", "no", "n", "0"}:
                return False
    return None


def _increment_cursor(cursor: str) -> str | None:
    try:
        return str(int(cursor) + 1)
    except Exception:
        return None


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


def _build_maersk_tracking_url(reference: str) -> str:
    return f"https://www.maersk.com/tracking/{quote(reference)}"
