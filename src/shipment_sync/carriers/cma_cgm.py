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
    extract_container_numbers,
    extract_final_destination_vessel_voyage,
    extract_event_state_hint,
    extract_eta_time,
    extract_first,
    extract_json_from_http_response,
    parse_event_time,
    to_dcsa_movement_name,
)
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus
from shipment_sync.playwright_runner import run_sync_playwright


class CmaCgmAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.use_playwright = _env_bool("CMA_CGM_USE_PLAYWRIGHT", default=False)
        self.playwright_required = _env_bool("CMA_CGM_PLAYWRIGHT_REQUIRED", default=False)
        self.playwright_headless = _env_bool("CMA_CGM_PLAYWRIGHT_HEADLESS", default=True)
        self.playwright_timeout_seconds = int(os.getenv("CMA_CGM_PLAYWRIGHT_TIMEOUT_SECONDS", "90"))
        self.playwright_wait_seconds = float(os.getenv("CMA_CGM_PLAYWRIGHT_WAIT_SECONDS", "8"))
        self.playwright_browser = os.getenv("CMA_CGM_PLAYWRIGHT_BROWSER", "chromium").strip() or "chromium"
        self.playwright_channel = os.getenv("CMA_CGM_PLAYWRIGHT_CHANNEL", "chrome").strip() or "chrome"
        self.playwright_locale = os.getenv("CMA_CGM_PLAYWRIGHT_LOCALE", "en-US").strip() or "en-US"
        self.playwright_warmup_url = (
            os.getenv("CMA_CGM_PLAYWRIGHT_WARMUP_URL", "https://www.cma-cgm.com/ebusiness/tracking").strip()
            or "https://www.cma-cgm.com/ebusiness/tracking"
        )
        self.playwright_user_agent = (
            os.getenv(
                "CMA_CGM_PLAYWRIGHT_USER_AGENT",
                (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            ).strip()
            or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
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
        playwright_error: Exception | None = None
        if self.use_playwright:
            try:
                return self._fetch_status_playwright(reference=reference, ref_type=ref_type, source_url=source_url)
            except Exception as exc:
                playwright_error = exc
                if self.playwright_required:
                    raise

        payload, source = self._fetch_payload(reference=reference, ref_type=ref_type)
        discovered_containers = extract_container_numbers(payload)
        recent_moves = _extract_moves(payload)
        latest_move = recent_moves[0] if recent_moves else None
        vessel_voyage = extract_final_destination_vessel_voyage(_extract_event_list(payload))
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
                discovered_containers=discovered_containers,
                raw_source=_append_raw_source(source, f"cma-playwright-error:{playwright_error}") if playwright_error else source,
                source_url=source_url,
                vessel_voyage=vessel_voyage,
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
            raw_source=_append_raw_source(source, f"cma-playwright-error:{playwright_error}") if playwright_error else source,
            source_url=source_url,
            movement_details=movement_details,
            vessel_voyage=vessel_voyage,
        )

    def _fetch_status_playwright(self, *, reference: str, ref_type: str, source_url: str) -> ShipmentStatus:
        def _run() -> ShipmentStatus:
            try:
                from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
                from playwright.sync_api import sync_playwright
            except Exception as exc:
                raise ValueError("Playwright is not installed. Run: pip install -e .[browser] && playwright install") from exc

            timeout_ms = max(1, self.playwright_timeout_seconds) * 1000
            wait_ms = max(0, int(self.playwright_wait_seconds * 1000))
            with sync_playwright() as p:
                browser_type = getattr(p, self.playwright_browser, None)
                if browser_type is None:
                    raise ValueError(f"Unsupported Playwright browser type: {self.playwright_browser}")

                launch_kwargs: dict[str, Any] = {
                    "headless": self.playwright_headless,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if self.playwright_channel and self.playwright_browser == "chromium":
                    launch_kwargs["channel"] = self.playwright_channel

                browser = browser_type.launch(**launch_kwargs)
                try:
                    context = browser.new_context(
                        user_agent=self.playwright_user_agent,
                        locale=self.playwright_locale,
                        viewport={"width": 1440, "height": 1000},
                    )
                    context.set_extra_http_headers(
                        {
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                        }
                    )
                    context.add_init_script(_STEALTH_INIT_SCRIPT)
                    page = context.new_page()
                    page.goto(self.playwright_warmup_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if wait_ms:
                        page.wait_for_timeout(min(wait_ms, 6000))
                    page.goto(source_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if wait_ms:
                        page.wait_for_timeout(wait_ms)

                    try:
                        page.get_by_text("Display Previous Moves", exact=False).first.click(timeout=3000)
                        if wait_ms:
                            page.wait_for_timeout(min(wait_ms, 4000))
                    except PlaywrightTimeoutError:
                        pass
                    except Exception:
                        pass

                    body_text = page.locator("body").inner_text(timeout=timeout_ms)
                    html = page.content()
                    combined_page = f"{body_text}\n{html}"
                    if "tracking details" not in body_text.lower() and _is_challenge_page(combined_page):
                        raise ValueError("CMA-CGM page blocked by anti-bot challenge")
                    return _status_from_playwright_text(
                        body_text,
                        reference=reference,
                        source_url=source_url,
                        raw_source=f"cma-playwright:{source_url}",
                    )
                finally:
                    browser.close()

        return run_sync_playwright(_run)

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


def _status_from_playwright_text(
    text: str,
    *,
    reference: str,
    source_url: str,
    raw_source: str,
) -> ShipmentStatus:
    if not text or "tracking details" not in text.lower():
        snippet = (text or "").strip().replace("\n", " ")[:240]
        raise ValueError(f"CMA-CGM tracking detail page did not contain tracking details: {snippet}")

    recent_moves = _extract_playwright_moves(text)
    latest_move = recent_moves[0] if recent_moves else None
    eta_time, eta_local_text = _extract_playwright_eta(text, recent_moves)
    vessel_voyage = _extract_playwright_final_vessel_voyage(text)
    status_text = _extract_playwright_status_text(text) or (latest_move.name if latest_move else None) or _eta_status_text(eta_time)
    containers = extract_container_numbers(text)
    if re.match(r"^[A-Za-z]{4}\d{7}$", reference) and reference not in containers:
        containers.insert(0, reference)

    return ShipmentStatus(
        status_text=status_text,
        location=latest_move.location if latest_move else None,
        event_time=latest_move.event_time if latest_move else None,
        eta_time=eta_time,
        eta_local_text=eta_local_text,
        latest_move=latest_move,
        recent_moves=recent_moves,
        discovered_containers=list(dict.fromkeys(containers)),
        raw_source=raw_source,
        source_url=source_url,
        movement_details=latest_move.name if latest_move else None,
        vessel_voyage=vessel_voyage,
    )


def _extract_playwright_status_text(text: str) -> str | None:
    lines = _clean_text_lines(text)
    for idx, line in enumerate(lines):
        if line.lower() == "tracking details":
            for candidate in lines[idx + 1 : idx + 8]:
                if candidate.lower() in {"shipment status", "container", "origin", "destination"}:
                    continue
                if re.match(r"^[A-Z][A-Z\s/-]{2,}$", candidate):
                    return candidate
    return None


def _extract_playwright_eta(text: str, moves: list[MovementEvent]) -> tuple[datetime | None, str | None]:
    lines = _clean_text_lines(text)
    for idx, line in enumerate(lines):
        if line.lower() == "arrived at pod" and idx + 2 < len(lines):
            parsed, local_text = _parse_cma_datetime(lines[idx + 1], lines[idx + 2])
            if parsed:
                return parsed, local_text

    pod_candidates = [
        move
        for move in moves
        if move.event_time is not None
        and (
            "Transport Arrived" in move.name
            or "Container Discharged" in move.name
        )
    ]
    if pod_candidates:
        best = max(pod_candidates, key=lambda move: move.event_time or datetime.min.replace(tzinfo=timezone.utc))
        return best.event_time, best.event_time_local_text
    return None, None


def _extract_playwright_moves(text: str) -> list[MovementEvent]:
    lines = _clean_text_lines(text)
    moves: list[MovementEvent] = []
    idx = 0
    while idx < len(lines):
        date_line = lines[idx]
        if not _is_cma_date_line(date_line):
            idx += 1
            continue
        if idx + 3 >= len(lines):
            break

        time_line = lines[idx + 1]
        move_label = lines[idx + 2]
        location = lines[idx + 3]
        if not _is_cma_time_line(time_line) or _is_cma_date_line(move_label):
            idx += 1
            continue

        parsed_time, local_text = _parse_cma_datetime(date_line, time_line)
        moves.append(
            MovementEvent(
                name=_cma_move_to_dcsa_name(move_label),
                location=location,
                event_time=parsed_time,
                event_time_local_text=local_text,
                event_state="actual",
            )
        )
        idx += 4
        if idx < len(lines) and _looks_like_vessel_line(lines[idx]):
            idx += 1

    return sorted(
        moves,
        key=lambda move: (move.event_time is not None, move.event_time.isoformat() if move.event_time else ""),
        reverse=True,
    )


def _extract_playwright_final_vessel_voyage(text: str) -> str | None:
    lines = _clean_text_lines(text)
    candidates: list[tuple[datetime, int, str]] = []
    idx = 0
    while idx < len(lines):
        date_line = lines[idx]
        if not _is_cma_date_line(date_line) or idx + 3 >= len(lines):
            idx += 1
            continue

        time_line = lines[idx + 1]
        move_label = lines[idx + 2]
        if not _is_cma_time_line(time_line):
            idx += 1
            continue

        vessel_line_idx = idx + 4
        vessel_line = lines[vessel_line_idx] if vessel_line_idx < len(lines) and _looks_like_vessel_line(lines[vessel_line_idx]) else None
        if vessel_line and _cma_final_vessel_event(move_label):
            parsed_time, _ = _parse_cma_datetime(date_line, time_line)
            if parsed_time is not None:
                candidates.append((parsed_time, idx, vessel_line))
        idx += 5 if vessel_line else 4

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _cma_final_vessel_event(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value).strip().upper()
    return normalized in {"VESSEL ARRIVAL", "DISCHARGED", "DISCHARGED IN TRANSHIPMENT"}


def _clean_text_lines(text: str) -> list[str]:
    ignored = {
        "accessible text",
        "display previous moves",
        "display more movements",
        "show more",
    }
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if line.lower() in ignored:
            continue
        lines.append(line)
    return lines


def _is_cma_date_line(value: str) -> bool:
    return bool(re.match(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), \d{2}-[A-Z]{3}-\d{4}$", value))


def _is_cma_time_line(value: str) -> bool:
    return bool(re.match(r"^\d{1,2}:\d{2}\s*(AM|PM)$", value, flags=re.IGNORECASE))


def _parse_cma_datetime(date_line: str, time_line: str) -> tuple[datetime | None, str | None]:
    cleaned = f"{date_line} {time_line}".replace(".", "").strip()
    for fmt in ("%A, %d-%b-%Y %I:%M %p", "%a, %d-%b-%Y %I:%M %p"):
        try:
            parsed = datetime.strptime(cleaned.title(), fmt).replace(tzinfo=timezone.utc)
            return parsed, parsed.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None, None


def _looks_like_vessel_line(value: str) -> bool:
    upper = value.upper()
    if _is_cma_date_line(value):
        return False
    if upper in _CMA_MOVE_LABELS:
        return False
    return bool(re.search(r"\([A-Z0-9]+\)", value))


def _cma_move_to_dcsa_name(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip().upper()
    if normalized == "EMPTY TO SHIPPER":
        return "Container Gated Out (GTOT)"
    if normalized == "LOADED ON BOARD":
        return "Container Loaded (LOAD)"
    if normalized == "VESSEL DEPARTURE":
        return "Transport Departed (DEPA)"
    if normalized == "VESSEL ARRIVAL":
        return "Transport Arrived (ARRI)"
    if normalized in {"DISCHARGED", "DISCHARGED IN TRANSHIPMENT"}:
        return "Container Discharged (DISC)"
    if normalized == "CONTAINER TO CONSIGNEE":
        return "Container Gated Out (GTOT)"
    if normalized == "EMPTY IN DEPOT":
        return "Container Gated In (GTIN)"
    return to_dcsa_movement_name(event={}, fallback_name=value)


_CMA_MOVE_LABELS = {
    "EMPTY TO SHIPPER",
    "LOADED ON BOARD",
    "VESSEL DEPARTURE",
    "VESSEL ARRIVAL",
    "DISCHARGED",
    "DISCHARGED IN TRANSHIPMENT",
    "CONTAINER TO CONSIGNEE",
    "EMPTY IN DEPOT",
}


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
        or "datadome" in normalized
        or "access is temporarily restricted" in normalized
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


def _append_raw_source(raw_source: str | None, addition: str) -> str:
    if not raw_source:
        return addition
    return f"{raw_source}; {addition}"


_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""
