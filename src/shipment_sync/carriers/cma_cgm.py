from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import time
from typing import Any
from urllib.parse import quote

import requests

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.common import (
    extract_event_state_hint,
    extract_eta_time,
    extract_first,
    extract_json_from_http_response,
    parse_event_time,
    to_dcsa_movement_name,
)
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus


class CmaCgmAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.url_template = os.getenv(
            "CMA_CGM_TRACKING_URL_TEMPLATE",
            "https://www.cma-cgm.com/ebusiness/tracking/detail/{reference}",
        ).strip()
        # CMA API can be configured as a full endpoint URL or as BASE + METHOD/METHOD_PATH.
        self.api_url = os.getenv("CMA_CGM_TRACKING_API_URL", "").strip()
        self.api_base_url = os.getenv("CMA_CGM_API_BASE_URL", "").strip()
        self.api_method = os.getenv("CMA_CGM_API_METHOD", "").strip()
        self.api_method_path = os.getenv("CMA_CGM_API_METHOD_PATH", "").strip()
        self.api_key = os.getenv("CMA_CGM_API_KEY", "").strip()
        # DCSA spec in CMA portal uses API key header "keyId".
        self.api_key_header = os.getenv("CMA_CGM_API_KEY_HEADER", "keyId").strip()
        # DCSA /events filters use equipmentReference and carrierBookingReference.
        self.ref_param = os.getenv("CMA_CGM_REF_PARAM", "").strip()
        self.container_ref_param = os.getenv("CMA_CGM_CONTAINER_REF_PARAM", "equipmentReference").strip()
        self.booking_ref_param = os.getenv("CMA_CGM_BOOKING_REF_PARAM", "carrierBookingReference").strip()
        self.type_param = os.getenv("CMA_CGM_TYPE_PARAM", "").strip()
        self.include_type_param = _env_bool("CMA_CGM_INCLUDE_TYPE_PARAM", default=False)
        self.booking_type_code = os.getenv("CMA_CGM_BOOKING_TYPE_CODE", "booking").strip()
        self.container_type_code = os.getenv("CMA_CGM_CONTAINER_TYPE_CODE", "container").strip()
        self.timeout_seconds = int(os.getenv("CMA_CGM_TIMEOUT_SECONDS", "45"))
        self.max_retries = int(os.getenv("CMA_CGM_MAX_RETRIES", "2"))
        self.retry_delay_seconds = float(os.getenv("CMA_CGM_RETRY_DELAY_SECONDS", "2"))
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
        reference, ref_type = _pick_reference(
            shipment=shipment,
            booking_code=self.booking_type_code,
            container_code=self.container_type_code,
        )
        source_url = _build_source_url(self.url_template, reference, ref_type)
        payload, source = self._fetch_payload(reference=reference, ref_type=ref_type)
        recent_moves = _extract_moves(payload)
        latest_move = recent_moves[0] if recent_moves else None
        eta_time = extract_eta_time(payload)
        eta_local_text = _extract_eta_raw(payload)
        if eta_time is None:
            derived_eta_time, derived_eta_local = _derive_eta_from_events(payload)
            eta_time = derived_eta_time
            if not eta_local_text:
                eta_local_text = derived_eta_local

        if self.eta_only_mode:
            return ShipmentStatus(
                status_text=_eta_status_text(eta_time),
                eta_time=eta_time,
                eta_local_text=eta_local_text,
                latest_move=latest_move,
                recent_moves=recent_moves,
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
            raw_source=source,
            source_url=source_url,
            movement_details=movement_details,
        )

    def _fetch_payload(self, *, reference: str, ref_type: str) -> tuple[dict[str, Any], str]:
        headers = {self.api_key_header: self.api_key} if self.api_key else {}
        api_url = self._resolve_api_url()
        api_mode_requested = bool(self.api_method or self.api_method_path or self.api_base_url)

        if api_url:
            request_url = self._format_reference_placeholders(api_url, reference=reference, ref_type=ref_type)
            response = self._request_with_retries(
                request_url,
                params=self._build_api_params(reference=reference, ref_type=ref_type),
                headers=headers,
            )
            payload = self._parse_payload(response)
            return payload, f"cma-api:{request_url}"

        if api_mode_requested:
            raise ValueError(
                "CMA-CGM API mode configured but endpoint is missing. "
                "Set CMA_CGM_TRACKING_API_URL or CMA_CGM_API_BASE_URL."
            )

        if not self.url_template:
            raise ValueError(
                "Set CMA_CGM_TRACKING_URL_TEMPLATE or CMA_CGM_TRACKING_API_URL "
                "(or CMA_CGM_API_BASE_URL with CMA_CGM_API_METHOD)"
            )

        url = self.url_template.format(reference=quote(reference), type=quote(ref_type))
        response = self._request_with_retries(url, headers=headers)
        payload = self._parse_payload(response)
        return payload, f"cma-web:{url}"

    def _resolve_api_url(self) -> str:
        if self.api_url:
            return self.api_url
        if not self.api_base_url:
            return ""

        method_path = self.api_method_path or _method_name_to_path(self.api_method)
        if not method_path:
            return self.api_base_url

        if "{method}" in self.api_base_url:
            method_token = quote(method_path.strip("/"))
            return self.api_base_url.format(method=method_token)

        normalized_method = method_path if method_path.startswith("/") else f"/{method_path}"
        return f"{self.api_base_url.rstrip('/')}{normalized_method}"

    def _build_api_params(self, *, reference: str, ref_type: str) -> dict[str, str] | None:
        params: dict[str, str] = {}
        ref_keys: list[str] = []
        if ref_type == self.container_type_code and self.container_ref_param:
            ref_keys.append(self.container_ref_param)
        if ref_type == self.booking_type_code and self.booking_ref_param:
            ref_keys.append(self.booking_ref_param)
        if self.ref_param:
            ref_keys.append(self.ref_param)

        for key in dict.fromkeys(ref_keys):
            params[key] = reference
        if self.include_type_param and self.type_param:
            params[self.type_param] = ref_type
        return params or None

    def _format_reference_placeholders(self, url: str, *, reference: str, ref_type: str) -> str:
        if "{reference}" not in url and "{type}" not in url and "{ref_type}" not in url:
            return url
        return url.format(
            reference=quote(reference),
            type=quote(ref_type),
            ref_type=quote(ref_type),
        )

    def _request_with_retries(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 400:
                    if _is_challenge_page(response.text):
                        raise ValueError("CMA-CGM endpoint blocked by anti-bot challenge")
                    response.raise_for_status()
                return response
            except ValueError:
                raise
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.retry_delay_seconds * (attempt + 1))

        if last_error:
            raise last_error
        raise RuntimeError("CMA-CGM request failed without specific error")

    def _parse_payload(self, response: requests.Response) -> dict[str, Any]:
        if _is_challenge_page(response.text):
            raise ValueError("CMA-CGM endpoint blocked by anti-bot challenge")
        try:
            payload = extract_json_from_http_response(response)
            if isinstance(payload, dict):
                return payload
            return {"data": payload}
        except Exception:
            raise ValueError("Could not parse JSON payload from CMA-CGM tracking response")


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
        move_name = _extract_move_name(event)
        moves.append(
            MovementEvent(
                name=move_name,
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


def _derive_eta_from_events(payload: dict[str, Any]) -> tuple[datetime | None, str | None]:
    events = _extract_event_list(payload)
    if not events:
        return None, None

    now_utc = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, str, bool]] = []
    for event in events:
        if not isinstance(event, dict):
            continue

        transport_code = str(event.get("transportEventTypeCode") or "").strip().upper()
        if transport_code != "ARRI":
            continue

        local_time_text = extract_first(
            event,
            [
                "eventDateTime",
                "eventLocalPortDate",
                "eventTime",
                "actualTime",
                "timestamp",
                "dateTime",
                "eventDate",
                "date",
            ],
        )
        parsed = parse_event_time(local_time_text)
        if parsed is None:
            continue

        classifier = str(event.get("eventClassifierCode") or "").strip().upper()
        is_estimated = classifier in {"PLN", "EST"}
        candidates.append((parsed, local_time_text or parsed.isoformat(), is_estimated))

    if not candidates:
        return None, None

    future_estimated = [c for c in candidates if c[2] and c[0] >= now_utc]
    if future_estimated:
        best = max(future_estimated, key=lambda item: item[0])
        return best[0], best[1]

    future_any = [c for c in candidates if c[0] >= now_utc]
    if future_any:
        best = max(future_any, key=lambda item: item[0])
        return best[0], best[1]

    estimated_any = [c for c in candidates if c[2]]
    if estimated_any:
        best = max(estimated_any, key=lambda item: item[0])
        return best[0], best[1]

    best = max(candidates, key=lambda item: item[0])
    return best[0], best[1]


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


def _extract_move_name(event: dict[str, Any]) -> str:
    raw_name = extract_first(
        event,
        [
            "eventName",
            "eventDescription",
            "milestoneName",
            "status",
            "movement",
            "description",
        ],
    )
    return to_dcsa_movement_name(event=event, fallback_name=raw_name)


def _eta_status_text(eta: datetime | None) -> str:
    if eta is None:
        return "ETA unavailable"
    return f"ETA {eta.isoformat()}"


def _build_source_url(template: str, reference: str, ref_type: str) -> str:
    cleaned = template.strip() if template else ""
    if not cleaned:
        cleaned = "https://www.cma-cgm.com/ebusiness/tracking/detail/{reference}"
    if "{reference}" in cleaned or "{type}" in cleaned:
        return cleaned.format(reference=quote(reference), type=quote(ref_type))
    return f"{cleaned.rstrip('/')}/{quote(reference)}"


def _is_challenge_page(body: str) -> bool:
    if not body:
        return False
    normalized = body.lower()
    return (
        "please enable js and disable any ad blocker" in normalized
        or "captcha" in normalized
        or "access denied" in normalized
        or "<title>cma-cgm.com</title>" in normalized
    )


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _method_name_to_path(method_name: str) -> str:
    normalized = (method_name or "").strip().lower()
    if not normalized:
        return ""
    if normalized == "searchmoveoncommercialcycle":
        return "/events"
    if normalized == "getmoveoncommercialcycle":
        return "/events/{reference}"
    return method_name
