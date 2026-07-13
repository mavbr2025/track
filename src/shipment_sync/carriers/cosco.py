from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any
from urllib.parse import quote
import uuid

import requests

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.common import (
    extract_container_numbers,
    extract_event_vessel_voyage,
    extract_eta_time,
    extract_event_state_hint,
    extract_final_destination_vessel_voyage,
    extract_first,
    parse_event_time,
    to_dcsa_movement_name,
)
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus


class CoscoAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)

        # COSCO integration mode:
        # - cop: official COSCO Open API with HMAC auth.
        # - legacy: public tracking web endpoints (often blocked by anti-bot).
        # - auto: COP first when keys exist, fallback to legacy.
        self.mode = os.getenv("COSCO_MODE", "cop").strip().lower() or "cop"

        # Official COP API (from https://github.com/cop-cos/COP)
        self.cop_base_url = os.getenv("COSCO_COP_BASE_URL", "https://api-pp.lines.coscoshipping.com").strip().rstrip("/")
        self.cop_service_prefix = os.getenv("COSCO_COP_SERVICE_PREFIX", "/service").strip() or "/service"
        self.cop_api_key = os.getenv("COSCO_COP_API_KEY", "").strip()
        self.cop_secret_key = os.getenv("COSCO_COP_SECRET_KEY", "").strip()
        self.cop_bl_number_type = os.getenv("COSCO_COP_BL_NUMBER_TYPE", "bl").strip() or "bl"
        self.cop_booking_number_type = os.getenv("COSCO_COP_BOOKING_NUMBER_TYPE", "bkg").strip() or "bkg"
        self.cop_container_number_type = os.getenv("COSCO_COP_CONTAINER_NUMBER_TYPE", "cntr").strip() or "cntr"
        self.cop_include_x_hmac = _env_bool("COSCO_COP_INCLUDE_X_HMAC", default=True)

        # Legacy public endpoints.
        self.use_legacy_api = _env_bool("COSCO_USE_API", default=True)
        self.legacy_api_base_url = os.getenv("COSCO_TRACKING_API_BASE_URL", "https://elines.coscoshipping.com/ebtracking").strip().rstrip("/")
        self.legacy_url_template = os.getenv(
            "COSCO_TRACKING_URL_TEMPLATE",
            "https://elines.coscoshipping.com/ebusiness/cargoTracking?trackingType={type}&number={reference}",
        ).strip()
        self.bl_type_code = os.getenv("COSCO_BL_TYPE_CODE", "BILLOFLADING").strip() or "BILLOFLADING"
        self.booking_type_code = os.getenv("COSCO_BOOKING_TYPE_CODE", "BOOKING").strip() or "BOOKING"
        self.container_type_code = os.getenv("COSCO_CONTAINER_TYPE_CODE", "CONTAINER").strip() or "CONTAINER"

        self.timeout_seconds = int(os.getenv("COSCO_TIMEOUT_SECONDS", "45"))
        self.max_retries = int(os.getenv("COSCO_MAX_RETRIES", "2"))
        self.retry_delay_seconds = float(os.getenv("COSCO_RETRY_DELAY_SECONDS", "2"))
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
        attempts = _build_reference_attempts(
            shipment=shipment,
            bl_type_code=self.bl_type_code,
            booking_type_code=self.booking_type_code,
            container_type_code=self.container_type_code,
        )
        if not attempts:
            raise ValueError("Missing booking/container number")

        last_error: Exception | None = None
        for reference, logical_type, legacy_type_code in attempts:
            source_url = _build_cosco_source_url(
                template=self.legacy_url_template,
                reference=reference,
                type_code=legacy_type_code,
            )
            try:
                payload, source = self._fetch_payload(
                    reference=reference,
                    logical_type=logical_type,
                    legacy_type_code=legacy_type_code,
                )
            except Exception as exc:
                last_error = exc
                continue

            status = _build_status_from_payload(payload=payload, source=source, source_url=source_url)
            if status is None:
                continue
            if self.eta_only_mode:
                status.status_text = _eta_status_text(status.eta_time)
            return status

        if last_error is not None:
            raise last_error
        raise ValueError("COSCO returned no usable status data for the given reference")

    def _fetch_payload(
        self,
        *,
        reference: str,
        logical_type: str,
        legacy_type_code: str,
    ) -> tuple[dict[str, Any], str]:
        mode = self.mode
        if mode not in {"cop", "legacy", "auto"}:
            mode = "cop"

        if mode in {"cop", "auto"} and self._has_cop_credentials():
            return self._fetch_payload_cop(reference=reference, logical_type=logical_type)

        if mode == "cop":
            raise ValueError(
                "adapter not configured: set COSCO_COP_API_KEY and COSCO_COP_SECRET_KEY "
                "(COP repo HMAC auth)"
            )

        if mode in {"legacy", "auto"}:
            return self._fetch_payload_legacy(reference=reference, type_code=legacy_type_code)

        raise ValueError("adapter not configured: invalid COSCO_MODE")

    def _fetch_payload_cop(self, *, reference: str, logical_type: str) -> tuple[dict[str, Any], str]:
        if not self.cop_base_url:
            raise ValueError("adapter not configured: set COSCO_COP_BASE_URL")

        number_type = self._cop_number_type(logical_type)
        service_prefix = self.cop_service_prefix
        if not service_prefix.startswith("/"):
            service_prefix = f"/{service_prefix}"
        request_path = f"{service_prefix}/info/tracking/{quote(reference)}?numberType={quote(number_type)}"
        headers = self._build_cop_hmac_headers(method="GET", request_path=request_path, body_str="")
        url = f"{self.cop_base_url}{request_path}"

        response = self._request_with_retries(url=url, headers=headers)
        payload = self._parse_json_response(response, error_prefix="COSCO COP")

        code = payload.get("code")
        if code not in (0, "0", None):
            message = payload.get("message")
            raise ValueError(f"COSCO COP API error code={code} message={message}")

        data = payload.get("data")
        if isinstance(data, dict):
            if str(data.get("type") or "").strip().lower() == "none":
                raise ValueError("COSCO COP returned no tracking information")
            if data.get("content") is None:
                raise ValueError("COSCO COP response missing tracking content")

        return payload, f"cosco-cop:{url}"

    def _fetch_payload_legacy(self, *, reference: str, type_code: str) -> tuple[dict[str, Any], str]:
        if self.use_legacy_api and self.legacy_api_base_url:
            api_url = self._build_legacy_api_url(reference=reference, type_code=type_code)
            if api_url:
                response = self._request_with_retries(url=api_url)
                payload = self._extract_legacy_payload(response)
                return payload, f"cosco-legacy-api:{api_url}"

        if not self.legacy_url_template:
            raise ValueError("Set COSCO_TRACKING_URL_TEMPLATE or COSCO_TRACKING_API_BASE_URL")

        url = self.legacy_url_template.format(reference=quote(reference), type=quote(type_code))
        response = self._request_with_cookie_challenge(url)
        payload = self._extract_legacy_payload(response)
        return payload, f"cosco-legacy-web:{url}"

    def _build_cop_hmac_headers(self, *, method: str, request_path: str, body_str: str) -> dict[str, str]:
        header_x_date = "X-Coscon-Date"
        header_content_md5 = "X-Coscon-Content-Md5"
        header_digest = "X-Coscon-Digest"
        header_authorization = "X-Coscon-Authorization"
        header_hmac = "X-Coscon-Hmac"

        current_time = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        content_md5 = hashlib.md5(uuid.uuid4().hex.encode("utf-8")).hexdigest()
        digest = "SHA-256=" + base64.b64encode(hashlib.sha256(body_str.encode("utf-8")).digest()).decode("utf-8")
        request_line = f"{method.upper()} {request_path} HTTP/1.1"
        sign_str = (
            f"{header_x_date}: {current_time}\n"
            f"{header_digest}: {digest}\n"
            f"{header_content_md5}: {content_md5}\n"
            f"{request_line}"
        )
        signature = base64.b64encode(
            hmac.new(
                key=self.cop_secret_key.encode("utf-8"),
                msg=sign_str.encode("utf-8"),
                digestmod=hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        authorization = (
            f'hmac username="{self.cop_api_key}",algorithm="hmac-sha1",'
            f'headers="{header_x_date} {header_digest} {header_content_md5} request-line",'
            f'signature="{signature}"'
        )

        headers = {
            header_x_date: current_time,
            header_content_md5: content_md5,
            header_digest: digest,
            header_authorization: authorization,
        }
        if self.cop_include_x_hmac:
            headers[header_hmac] = content_md5
        return headers

    def _request_with_retries(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout_seconds)
                if response.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds * (attempt + 1))
                    continue
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.retry_delay_seconds * (attempt + 1))

        if last_error:
            raise last_error
        raise RuntimeError("COSCO request failed without specific error")

    def _parse_json_response(self, response: requests.Response, *, error_prefix: str) -> dict[str, Any]:
        if response.status_code >= 400:
            snippet = (response.text or "").strip().replace("\n", " ")[:300]
            raise ValueError(f"{error_prefix} request failed HTTP {response.status_code}: {snippet}")
        try:
            payload = response.json()
        except Exception:
            snippet = (response.text or "").strip().replace("\n", " ")[:300]
            raise ValueError(f"{error_prefix} response is not JSON: {snippet}")
        if isinstance(payload, dict):
            return payload
        return {"data": payload}

    def _extract_legacy_payload(self, response: requests.Response) -> dict[str, Any]:
        content_type = (response.headers.get("content-type") or "").lower()
        body = response.text or ""

        if response.status_code >= 400:
            if _is_legacy_block_page(body):
                raise ValueError("COSCO endpoint blocked by bot protection")
            snippet = body.strip().replace("\n", " ")[:240]
            raise ValueError(f"COSCO legacy endpoint returned HTTP {response.status_code}: {snippet}")

        if "json" in content_type:
            parsed = response.json()
            if isinstance(parsed, dict):
                return parsed
            return {"data": parsed}

        if _is_legacy_block_page(body):
            raise ValueError("COSCO endpoint blocked by bot protection")

        embedded = _extract_embedded_json(body)
        if embedded is not None:
            return embedded

        raise ValueError("Could not parse COSCO legacy response payload")

    def _request_with_cookie_challenge(self, url: str) -> requests.Response:
        response = self._request_with_retries(url=url)
        if _is_legacy_block_page(response.text):
            host = "elines.coscoshipping.com"
            if _apply_cookie_script(self.session, response.text, host=host):
                response = self._request_with_retries(url=url)
        return response

    def _build_legacy_api_url(self, *, reference: str, type_code: str) -> str | None:
        normalized_type = type_code.strip().upper()
        encoded_ref = quote(reference)
        if normalized_type == self.container_type_code.upper():
            return f"{self.legacy_api_base_url}/public/containers/{encoded_ref}"
        if normalized_type == self.booking_type_code.upper():
            return f"{self.legacy_api_base_url}/public/booking/{encoded_ref}"
        if normalized_type == self.bl_type_code.upper():
            return f"{self.legacy_api_base_url}/public/bill/{encoded_ref}"
        return None

    def _cop_number_type(self, logical_type: str) -> str:
        if logical_type == "container":
            return self.cop_container_number_type
        if logical_type == "booking":
            return self.cop_booking_number_type
        return self.cop_bl_number_type

    def _has_cop_credentials(self) -> bool:
        return bool(self.cop_api_key and self.cop_secret_key)


def _build_reference_attempts(
    *,
    shipment: ShipmentRef,
    bl_type_code: str,
    booking_type_code: str,
    container_type_code: str,
) -> list[tuple[str, str, str]]:
    attempts: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for raw_value, logical_type, legacy_type_code in (
        (shipment.container_no, "container", container_type_code),
        (shipment.booking_no, "booking", booking_type_code),
        # Some tasks may contain BL in booking field.
        (shipment.booking_no, "bl", bl_type_code),
    ):
        reference = _normalize_reference(raw_value or "")
        if not reference:
            continue
        item = (reference, logical_type, legacy_type_code)
        if item in seen:
            continue
        attempts.append(item)
        seen.add(item)
    return attempts


def _normalize_reference(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return cleaned
    tokens = [tok.strip() for tok in re.split(r"[,\s]+", cleaned) if tok.strip()]
    if not tokens:
        return cleaned
    for token in tokens:
        if re.match(r"^[A-Za-z]{4}\d{7}$", token):
            return token
    return tokens[0]


def _extract_embedded_json(body: str) -> dict[str, Any] | None:
    next_match = re.search(
        r"<script[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if next_match:
        try:
            payload = json.loads(next_match.group(1))
            if isinstance(payload, dict):
                return payload
            return {"data": payload}
        except Exception:
            return None

    for pattern in (
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;",
        r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;",
    ):
        match = re.search(pattern, body, re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
            if isinstance(payload, dict):
                return payload
            return {"data": payload}
        except Exception:
            continue
    return None


def _build_status_from_payload(
    *,
    payload: dict[str, Any],
    source: str,
    source_url: str,
) -> ShipmentStatus | None:
    normalized_payload = _normalize_cosco_payload(payload)
    events = _extract_event_list(normalized_payload)
    recent_moves = _extract_moves(normalized_payload)
    latest_move = recent_moves[0] if recent_moves else None
    eta_time = extract_eta_time(normalized_payload)
    eta_local_text = _extract_eta_raw(normalized_payload)
    status_hint = extract_first(normalized_payload, ["status", "shipmentStatus", "transportStatus", "latestEvent", "eventDescription"])

    status_text = (latest_move.name if latest_move else None) or status_hint or _eta_status_text(eta_time)
    location = (
        (latest_move.location if latest_move else None)
        or extract_first(normalized_payload, ["location", "locationName", "city", "port", "eventLocation", "nodeName", "unLocCode"])
    )
    event_time = (latest_move.event_time if latest_move else None) or parse_event_time(
        extract_first(normalized_payload, ["eventTime", "eventDateTime", "actualTime", "timestamp", "dateTime", "date", "eventDate"])
    )
    movement_details = extract_first(
        normalized_payload,
        ["eventDescription", "milestoneName", "nodeName", "transportStatus", "cargoStatus", "eventType"],
    )
    discovered_containers = extract_container_numbers(normalized_payload)

    if not any([recent_moves, eta_time, eta_local_text, location, movement_details, status_hint]):
        return None

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
        vessel_voyage=extract_final_destination_vessel_voyage(events),
    )


def _normalize_cosco_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # COP response shape:
    # { code, message, data: { type, numberType, content: {...} } }
    # Normalize to a flatter structure so generic extractors can work.
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload
    content = data.get("content")
    if not isinstance(content, dict):
        return payload

    normalized: dict[str, Any] = dict(content)
    normalized["cosco_meta_type"] = data.get("type")
    normalized["cosco_meta_number_type"] = data.get("numberType")
    if isinstance(content.get("trackingPath"), dict):
        normalized["trackingPath"] = content["trackingPath"]
    if isinstance(content.get("actualShipment"), list):
        normalized["actualShipment"] = content["actualShipment"]
    if isinstance(content.get("containerStatus"), list):
        normalized["containerStatus"] = content["containerStatus"]
    if isinstance(content.get("containers"), list):
        normalized["containers"] = content["containers"]
    return normalized


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
                "locationDateTime",
                "actualArrivalDate",
                "estimatedDateOfArrival",
                "actualDepartureDate",
                "expectedDateOfDeparture",
            ],
        )
        state_hint = extract_event_state_hint(event, extra_keys=["label"])
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
                "containerNumberStatus",
                "label",
            ],
        )
        moves.append(
            MovementEvent(
                name=to_dcsa_movement_name(event=event, fallback_name=raw_name),
                location=extract_first(
                    event,
                    ["locationName", "location", "city", "port", "eventLocation", "nodeName", "unLocCode", "portOfDischarge", "portOfLoading"],
                ),
                event_time=parse_event_time(local_time_text),
                event_time_local_text=local_time_text,
                event_state=_normalize_event_state(state_hint),
                vessel_voyage=extract_event_vessel_voyage(event),
            )
        )

    return sorted(
        moves,
        key=lambda m: (m.event_time is not None, m.event_time.isoformat() if m.event_time else ""),
        reverse=True,
    )


def _extract_event_list(payload: dict[str, Any]) -> list[Any]:
    # First try generic direct event-like arrays.
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

    # COP-specific shapes.
    container_events: list[dict[str, Any]] = []
    containers = payload.get("containers")
    if isinstance(containers, list):
        for container in containers:
            if not isinstance(container, dict):
                continue
            history = container.get("containerHistorys")
            if isinstance(history, list):
                container_events.extend([h for h in history if isinstance(h, dict)])
            circle = container.get("containerCircleStatus")
            if isinstance(circle, list):
                container_events.extend([c for c in circle if isinstance(c, dict)])
    if container_events:
        return container_events

    status_events: list[dict[str, Any]] = []
    container_status = payload.get("containerStatus")
    if isinstance(container_status, list):
        for status_group in container_status:
            if not isinstance(status_group, dict):
                continue
            label = status_group.get("label")
            group_items = status_group.get("containers")
            if not isinstance(group_items, list):
                continue
            for item in group_items:
                if not isinstance(item, dict):
                    continue
                merged = dict(item)
                if label and "label" not in merged:
                    merged["label"] = label
                status_events.append(merged)
    if status_events:
        return status_events

    shipment = payload.get("actualShipment")
    if isinstance(shipment, list):
        return [s for s in shipment if isinstance(s, dict)]

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
            "estimatedDateOfArrival",
            "actualArrivalDate",
            "cgoAvailTm",
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


def _build_cosco_source_url(template: str, reference: str, type_code: str) -> str:
    if template:
        return template.format(reference=quote(reference), type=quote(type_code))
    return (
        "https://elines.coscoshipping.com/ebusiness/cargoTracking"
        f"?trackingType={quote(type_code)}&number={quote(reference)}"
    )


def _is_legacy_block_page(body: str) -> bool:
    if not body:
        return False
    normalized = body.lower()
    return (
        "this page can't be displayed" in normalized
        or "页面无法显示" in body
        or "support@coscon.com" in normalized
        or "<title>error</title>" in normalized
    )


def _apply_cookie_script(session: requests.Session, body: str, *, host: str) -> bool:
    applied = False
    for match in re.finditer(r'document\.cookie\s*=\s*"([^"]+)"', body, flags=re.IGNORECASE):
        cookie_line = match.group(1)
        parts = cookie_line.split(";", 1)
        if not parts:
            continue
        name_value = parts[0].strip()
        if "=" not in name_value:
            continue
        name, value = name_value.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        session.cookies.set(name, value, domain=host, path="/")
        applied = True
    return applied


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
