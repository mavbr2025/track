from __future__ import annotations

from datetime import datetime
import os
import re
import time
from typing import Any

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.common import extract_first, parse_event_time, to_dcsa_movement_name
from shipment_sync.carriers.generic_line import GenericLineAdapter
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus


class MscAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.use_playwright = _env_bool("MSC_USE_PLAYWRIGHT", default=True)
        self.playwright_required = _env_bool("MSC_PLAYWRIGHT_REQUIRED", default=False)
        self.playwright_headless = _env_bool("MSC_PLAYWRIGHT_HEADLESS", default=True)
        self.playwright_timeout_seconds = int(os.getenv("MSC_PLAYWRIGHT_TIMEOUT_SECONDS", "90"))
        self.playwright_request_delay_seconds = float(os.getenv("MSC_PLAYWRIGHT_REQUEST_DELAY_SECONDS", "0.5"))
        self.playwright_browser = os.getenv("MSC_PLAYWRIGHT_BROWSER", "chromium").strip() or "chromium"
        self.playwright_channel = os.getenv("MSC_PLAYWRIGHT_CHANNEL", "chrome").strip() or "chrome"
        self.playwright_locale = os.getenv("MSC_PLAYWRIGHT_LOCALE", "en-US").strip() or "en-US"
        self.playwright_user_agent = (
            os.getenv(
                "MSC_PLAYWRIGHT_USER_AGENT",
                (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            ).strip()
            or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )
        self.tracking_page_url = (
            os.getenv("MSC_PLAYWRIGHT_TRACKING_URL", "https://www.msc.com/en/track-a-shipment").strip()
            or "https://www.msc.com/en/track-a-shipment"
        )
        self.tracking_api_url = (
            os.getenv("MSC_PLAYWRIGHT_API_ENDPOINT", "https://www.msc.com/api/feature/tools/TrackingInfo").strip()
            or "https://www.msc.com/api/feature/tools/TrackingInfo"
        )
        self.max_retries = int(os.getenv("MSC_MAX_RETRIES", "2"))
        self.retry_delay_seconds = float(os.getenv("MSC_RETRY_DELAY_SECONDS", "2"))

        self.generic_fallback = GenericLineAdapter(
            env_prefix="MSC",
            line_label="MSC",
            default_page_url_template="https://www.msc.com/en/track-a-shipment",
            challenge_markers=("captcha", "access denied", "forbidden", "please enable javascript"),
        )

    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        playwright_error: Exception | None = None
        if self.use_playwright:
            try:
                return self._fetch_status_playwright(shipment)
            except Exception as exc:
                playwright_error = exc
                if self.playwright_required:
                    raise

        try:
            status = self.generic_fallback.fetch_status(shipment)
        except Exception as fallback_error:
            if playwright_error is not None:
                raise ValueError(f"MSC Playwright failed ({playwright_error}); fallback failed ({fallback_error})")
            raise

        if playwright_error is not None:
            status.raw_source = _append_raw_source(
                status.raw_source,
                f"msc-playwright-error:{playwright_error}",
            )
        return status

    def _fetch_status_playwright(self, shipment: ShipmentRef) -> ShipmentStatus:
        attempts = _build_reference_attempts(shipment)
        if not attempts:
            raise ValueError("Missing booking/container number")

        last_error: Exception | None = None
        for reference, tracking_mode in attempts:
            for attempt in range(self.max_retries + 1):
                try:
                    payload, source = self._playwright_request(
                        reference=reference,
                        tracking_mode=tracking_mode,
                    )
                    return _status_from_payload(
                        payload=payload,
                        source=source,
                        source_url=self.tracking_page_url,
                        eta_only_mode=self.eta_only_mode,
                    )
                except Exception as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    time.sleep(self.retry_delay_seconds * (attempt + 1))

        if last_error:
            raise last_error
        raise ValueError("MSC Playwright request failed without specific error")

    def _playwright_request(self, *, reference: str, tracking_mode: str) -> tuple[dict[str, Any], str]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise ValueError("Playwright is not installed. Run: pip install -e .[browser] && playwright install") from exc

        timeout_ms = max(1, self.playwright_timeout_seconds) * 1000
        with sync_playwright() as p:
            browser_type = getattr(p, self.playwright_browser, None)
            if browser_type is None:
                raise ValueError(f"Unsupported Playwright browser type: {self.playwright_browser}")

            launch_kwargs: dict[str, Any] = {"headless": self.playwright_headless}
            if self.playwright_channel and self.playwright_browser == "chromium":
                launch_kwargs["channel"] = self.playwright_channel

            browser = browser_type.launch(**launch_kwargs)
            try:
                context = browser.new_context(
                    user_agent=self.playwright_user_agent,
                    locale=self.playwright_locale,
                )
                page = context.new_page()
                page.goto(self.tracking_page_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_selector(
                    "input[name='__RequestVerificationToken']",
                    timeout=timeout_ms,
                    state="attached",
                )

                html = page.content().lower()
                if "access denied" in html:
                    raise ValueError("MSC page access denied in browser session")

                token = page.locator("input[name='__RequestVerificationToken']").first.get_attribute("value")
                if not token:
                    raise ValueError("MSC request verification token not found in page")

                if self.playwright_request_delay_seconds > 0:
                    page.wait_for_timeout(int(self.playwright_request_delay_seconds * 1000))

                response = context.request.post(
                    self.tracking_api_url,
                    form={
                        "__RequestVerificationToken": token,
                        "trackingMode": tracking_mode,
                        "trackingNumber": reference,
                    },
                    headers={
                        "Origin": "https://www.msc.com",
                        "Referer": self.tracking_page_url,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=timeout_ms,
                )
                if response.status >= 400:
                    snippet = response.text().strip().replace("\n", " ")[:240]
                    raise ValueError(f"MSC TrackingInfo failed HTTP {response.status}: {snippet}")
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("MSC TrackingInfo returned non-object JSON payload")
                return payload, f"msc-playwright:{self.tracking_api_url}"
            finally:
                browser.close()


def _build_reference_attempts(shipment: ShipmentRef) -> list[tuple[str, str]]:
    attempts: list[tuple[str, str]] = []
    if shipment.container_no:
        ref = _normalize_reference(shipment.container_no)
        if ref:
            # MSC UI mode "0" = Container/Bill of Lading Number.
            attempts.append((ref, "0"))
    if shipment.booking_no:
        ref = _normalize_reference(shipment.booking_no)
        if ref:
            # MSC UI mode "1" = Booking Number.
            attempts.append((ref, "1"))
    return attempts


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


def _status_from_payload(
    *,
    payload: dict[str, Any],
    source: str,
    source_url: str,
    eta_only_mode: bool,
) -> ShipmentStatus:
    if payload.get("IsSuccess") is not True:
        message = (
            extract_first(payload, ["ErrorMessage", "errorMessage", "Message", "message"])
            or _extract_unsuccessful_payload_message(payload)
            or "MSC TrackingInfo returned unsuccessful response"
        )
        raise ValueError(message)

    data = payload.get("Data")
    if not isinstance(data, dict):
        raise ValueError("MSC TrackingInfo payload missing Data object")

    bill = _pick_bill(data)
    container = _pick_container(bill)
    events = _extract_events(container)
    recent_moves = _events_to_moves(events)
    latest_move = recent_moves[0] if recent_moves else None

    eta_local_text = _extract_eta_local_text(container, bill)
    eta_time = parse_event_time(eta_local_text)
    if eta_time is None:
        eta_time, eta_local_text = _derive_eta_from_moves(recent_moves)

    if eta_only_mode:
        return ShipmentStatus(
            status_text=_eta_status_text(eta_time),
            eta_time=eta_time,
            eta_local_text=eta_local_text,
            latest_move=latest_move,
            recent_moves=recent_moves,
            raw_source=source,
            source_url=source_url,
        )

    return ShipmentStatus(
        status_text=(latest_move.name if latest_move else _eta_status_text(eta_time)),
        location=latest_move.location if latest_move else None,
        event_time=latest_move.event_time if latest_move else None,
        eta_time=eta_time,
        eta_local_text=eta_local_text,
        latest_move=latest_move,
        recent_moves=recent_moves,
        raw_source=source,
        source_url=source_url,
        movement_details=latest_move.name if latest_move else None,
    )


def _pick_bill(data: dict[str, Any]) -> dict[str, Any]:
    bills = data.get("BillOfLadings")
    if isinstance(bills, list):
        for bill in bills:
            if isinstance(bill, dict):
                return bill
    return {}


def _pick_container(bill: dict[str, Any]) -> dict[str, Any]:
    containers = bill.get("ContainersInfo")
    if isinstance(containers, list):
        for container in containers:
            if isinstance(container, dict):
                return container
    return {}


def _extract_events(container: dict[str, Any]) -> list[dict[str, Any]]:
    events = container.get("Events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _events_to_moves(events: list[dict[str, Any]]) -> list[MovementEvent]:
    moves: list[MovementEvent] = []
    for event in events:
        description = _extract_description(event)
        detail = _extract_detail(event)
        fallback_name = description
        if detail and description:
            fallback_name = f"{description} ({detail})"

        local_time_text = extract_first(event, ["Date", "EventDate", "eventDateTime", "date"])
        location = extract_first(event, ["Location", "location", "LocationName", "locationName", "Port", "port"])
        state_hint = extract_first(event, ["eventClassifierCode", "trigger", "eventType", "status", "Detail"])

        moves.append(
            MovementEvent(
                name=to_dcsa_movement_name(event=event, fallback_name=fallback_name),
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


def _extract_description(event: dict[str, Any]) -> str | None:
    return extract_first(event, ["Description", "description", "EventName", "eventName", "LatestMove", "latestMove"])


def _extract_detail(event: dict[str, Any]) -> str | None:
    detail = event.get("Detail")
    if isinstance(detail, list):
        parts = [str(item).strip() for item in detail if str(item).strip()]
        if parts:
            return ", ".join(parts)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return None


def _extract_eta_local_text(container: dict[str, Any], bill: dict[str, Any]) -> str | None:
    eta = extract_first(container, ["PodEtaDate", "POD ETA", "Eta", "ETA"])
    if eta:
        return eta
    general = bill.get("GeneralTrackingInfo")
    if isinstance(general, dict):
        return extract_first(general, ["FinalPodEtaDate", "PodEtaDate", "ETA", "Eta"])
    return None


def _derive_eta_from_moves(moves: list[MovementEvent]) -> tuple[datetime | None, str | None]:
    for move in moves:
        if "arriv" in move.name.lower() and move.event_time is not None:
            return move.event_time, move.event_time_local_text
    return None, None


def _eta_status_text(eta: datetime | None) -> str:
    if eta is None:
        return "ETA unavailable"
    return f"ETA {eta.isoformat()}"


def _append_raw_source(raw_source: str | None, extra: str) -> str:
    base = (raw_source or "").strip()
    if not base:
        return extra
    return f"{base} | {extra}"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _extract_unsuccessful_payload_message(payload: dict[str, Any]) -> str | None:
    data = payload.get("Data")
    if isinstance(data, str):
        cleaned = data.strip()
        return cleaned or None
    return None
