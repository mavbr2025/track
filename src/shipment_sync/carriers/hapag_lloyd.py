import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.common import (
    extract_container_numbers,
    extract_event_vessel_voyage,
    extract_event_state_hint,
    extract_eta_time,
    extract_first,
    extract_final_destination_vessel_voyage,
    extract_json_from_http_response,
    get_with_retries,
    parse_event_time,
    to_dcsa_movement_name,
)
from shipment_sync.models import MovementEvent, ShipmentRef, ShipmentStatus
from shipment_sync.playwright_runner import run_sync_playwright


class HapagLloydAdapter(CarrierAdapter):
    def __init__(self) -> None:
        self.eta_only_mode = _env_bool("SHIPMENT_ETA_ONLY", default=True)
        self.use_playwright = _env_bool("HAPAG_USE_PLAYWRIGHT", default=True)
        self.playwright_required = _env_bool("HAPAG_PLAYWRIGHT_REQUIRED", default=False)
        self.playwright_headless = _env_bool("HAPAG_PLAYWRIGHT_HEADLESS", default=True)
        self.playwright_timeout_seconds = int(os.getenv("HAPAG_PLAYWRIGHT_TIMEOUT_SECONDS", "90"))
        self.playwright_request_delay_seconds = float(os.getenv("HAPAG_PLAYWRIGHT_REQUEST_DELAY_SECONDS", "6"))
        self.playwright_warmup_seconds = float(os.getenv("HAPAG_PLAYWRIGHT_WARMUP_SECONDS", "4"))
        self.playwright_browser = os.getenv("HAPAG_PLAYWRIGHT_BROWSER", "chromium").strip() or "chromium"
        self.playwright_channel = os.getenv("HAPAG_PLAYWRIGHT_CHANNEL", "chrome").strip() or "chrome"
        self.playwright_locale = os.getenv("HAPAG_PLAYWRIGHT_LOCALE", "en-US").strip() or "en-US"
        self.playwright_challenge_timeout_seconds = int(os.getenv("HAPAG_PLAYWRIGHT_CHALLENGE_TIMEOUT_SECONDS", "20"))
        self.playwright_challenge_reload_attempts = int(os.getenv("HAPAG_PLAYWRIGHT_CHALLENGE_RELOAD_ATTEMPTS", "1"))
        self.playwright_session_reuse = _env_bool("HAPAG_PLAYWRIGHT_SESSION_REUSE", default=True)
        self.playwright_user_agent = (
            os.getenv(
                "HAPAG_PLAYWRIGHT_USER_AGENT",
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
        self.playwright_view = os.getenv("HAPAG_PLAYWRIGHT_VIEW", "S8510").strip() or "S8510"
        self.playwright_container_url_template = os.getenv(
            "HAPAG_PLAYWRIGHT_CONTAINER_URL_TEMPLATE",
            (
                "https://www.hapag-lloyd.com/en/online-business/track/"
                "track-by-container-solution.html?view={view}&container={reference}"
            ),
        ).strip()
        self.playwright_booking_url_template = os.getenv(
            "HAPAG_PLAYWRIGHT_BOOKING_URL_TEMPLATE",
            (
                "https://www.hapag-lloyd.com/en/online-business/track/"
                "track-by-booking-solution.html?view={view}&booking={reference}"
            ),
        ).strip()
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
        self._playwright_runtime: Any | None = None
        self._playwright_browser_handle: Any | None = None
        self._playwright_context: Any | None = None
        self._playwright_page: Any | None = None
        self._playwright_warmed_mode: str | None = None

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
            return self._fetch_status_api(shipment)
        except Exception as fallback_error:
            if playwright_error is not None:
                raise ValueError(f"Hapag page mode failed ({playwright_error}); fallback failed ({fallback_error})")
            raise

    def _fetch_status_playwright(self, shipment: ShipmentRef) -> ShipmentStatus:
        attempts = _build_reference_attempts(shipment, self.booking_type_code, self.container_type_code)
        if not attempts:
            raise ValueError("Missing booking/container number")

        last_error: Exception | None = None
        for reference, ref_type_code in attempts:
            for attempt in range(self.max_retries + 1):
                try:
                    return self._playwright_request(reference=reference, ref_type_code=ref_type_code)
                except Exception as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    time.sleep(self.retry_delay_seconds * (attempt + 1))

        if last_error is not None:
            raise last_error
        raise ValueError("Hapag page mode failed without a specific error")

    def _fetch_status_api(self, shipment: ShipmentRef) -> ShipmentStatus:
        self._validate_configuration()
        reference, ref_type_code = _pick_reference(shipment, self.booking_type_code, self.container_type_code)
        reference = _normalize_reference(reference)
        source_url = _build_source_url(self.page_url_template, reference, ref_type_code)
        payload, source = self._fetch_payload(reference, ref_type_code)

        eta_time = extract_eta_time(payload)
        eta_local_text = _extract_eta_raw(payload)
        recent_moves = _extract_moves(payload)
        vessel_voyage = extract_final_destination_vessel_voyage(_extract_event_list(payload))
        discovered_containers = extract_container_numbers(payload)

        if self.eta_only_mode:
            return ShipmentStatus(
                status_text=_eta_status_text(eta_time),
                eta_time=eta_time,
                eta_local_text=eta_local_text,
                latest_move=recent_moves[0] if recent_moves else None,
                recent_moves=recent_moves,
                discovered_containers=discovered_containers,
                raw_source=source,
                source_url=source_url,
                vessel_voyage=vessel_voyage,
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
            discovered_containers=discovered_containers,
            raw_source=source,
            source_url=source_url,
            movement_details=movement_details,
            vessel_voyage=vessel_voyage,
        )

    def _playwright_request(self, *, reference: str, ref_type_code: str) -> ShipmentStatus:
        def _run() -> ShipmentStatus:
            try:
                from playwright.sync_api import sync_playwright
            except Exception as exc:
                raise ValueError("Playwright is not installed. Run: pip install -e .[browser]") from exc

            timeout_ms = max(1, self.playwright_timeout_seconds) * 1000
            normalized_mode = ref_type_code.strip().lower()
            landing_url = self._build_playwright_landing_url(ref_type_code=normalized_mode)
            target_url = self._build_playwright_url(reference=reference, ref_type_code=ref_type_code)
            try:
                page = self._get_or_create_playwright_page(sync_playwright)
                self._warm_playwright_session(
                    page,
                    landing_url=landing_url,
                    mode=normalized_mode,
                    timeout_ms=timeout_ms,
                )
                page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                _wait_for_hapag_result_page(
                    page,
                    timeout_ms=timeout_ms,
                    challenge_timeout_seconds=self.playwright_challenge_timeout_seconds,
                    reload_attempts=self.playwright_challenge_reload_attempts,
                    post_load_delay_seconds=self.playwright_request_delay_seconds,
                )

                html = page.content()
                return _status_from_page(
                    html=html,
                    source_url=page.url,
                    eta_only_mode=self.eta_only_mode,
                )
            except Exception as exc:
                if _should_reset_playwright_session(exc):
                    self._reset_playwright_session()
                raise
            finally:
                if not self.playwright_session_reuse:
                    self._reset_playwright_session()

        return run_sync_playwright(_run)

    def _validate_configuration(self) -> None:
        if self.url_template:
            return
        if not self.api_url:
            raise ValueError("adapter not configured: set HAPAG_TRACKING_URL_TEMPLATE or HAPAG_TRACKING_API_URL")
        if self._has_api_auth():
            return
        raise ValueError(
            "adapter not configured: Hapag API requires auth; set "
            "HAPAG_BEARER_TOKEN, HAPAG_OAUTH_TOKEN_URL + HAPAG_OAUTH_CLIENT_ID + "
            "HAPAG_OAUTH_CLIENT_SECRET, HAPAG_API_KEY, or HAPAG_CLIENT_ID + HAPAG_CLIENT_SECRET"
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

    def _has_api_auth(self) -> bool:
        if self.bearer_token:
            return True
        if self.api_key:
            return True
        if self.client_id and self.client_secret:
            return True
        if self.oauth_token_url and self.oauth_client_id and self.oauth_client_secret:
            return True
        return False

    def _build_playwright_url(self, *, reference: str, ref_type_code: str) -> str:
        template = self.playwright_booking_url_template
        if ref_type_code.strip().lower() in {"container", "equipment", "cntr", "c"}:
            template = self.playwright_container_url_template
        return template.format(
            reference=quote(reference),
            type=quote(ref_type_code),
            view=quote(self.playwright_view),
        )

    def _build_playwright_landing_url(self, *, ref_type_code: str) -> str:
        template = self.playwright_booking_url_template
        reference = ""
        if ref_type_code in {"container", "equipment", "cntr", "c"}:
            template = self.playwright_container_url_template
        return template.format(
            reference=quote(reference),
            type=quote(ref_type_code),
            view=quote(self.playwright_view),
        )

    def _get_or_create_playwright_page(self, sync_playwright: Any) -> Any:
        if self._playwright_page is not None:
            return self._playwright_page

        runtime = sync_playwright().start()
        browser_type = getattr(runtime, self.playwright_browser, None)
        if browser_type is None:
            runtime.stop()
            raise ValueError(f"Unsupported Playwright browser type: {self.playwright_browser}")

        launch_kwargs: dict[str, Any] = {
            "headless": self.playwright_headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if self.playwright_channel and self.playwright_browser == "chromium":
            launch_kwargs["channel"] = self.playwright_channel

        browser = browser_type.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=self.playwright_user_agent,
            locale=self.playwright_locale,
            viewport={"width": 1440, "height": 900},
        )
        context.set_extra_http_headers(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        context.add_init_script(_STEALTH_INIT_SCRIPT)
        page = context.new_page()

        self._playwright_runtime = runtime
        self._playwright_browser_handle = browser
        self._playwright_context = context
        self._playwright_page = page
        self._playwright_warmed_mode = None
        return page

    def _warm_playwright_session(self, page: Any, *, landing_url: str, mode: str, timeout_ms: int) -> None:
        if self.playwright_session_reuse and self._playwright_warmed_mode == mode:
            return

        page.goto(landing_url, wait_until="domcontentloaded", timeout=timeout_ms)
        self._perform_human_warmup(page)
        _wait_for_hapag_challenge_stage(
            page,
            timeout_ms=timeout_ms,
            challenge_timeout_seconds=self.playwright_challenge_timeout_seconds,
            reload_attempts=self.playwright_challenge_reload_attempts,
        )
        self._playwright_warmed_mode = mode

    def _perform_human_warmup(self, page: Any) -> None:
        page.mouse.move(220, 180)
        page.wait_for_timeout(500)
        page.mouse.move(480, 260)
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(500)
        page.mouse.wheel(0, -200)
        if self.playwright_warmup_seconds > 0:
            page.wait_for_timeout(int(self.playwright_warmup_seconds * 1000))

    def _reset_playwright_session(self) -> None:
        page = self._playwright_page
        context = self._playwright_context
        browser = self._playwright_browser_handle
        runtime = self._playwright_runtime

        self._playwright_page = None
        self._playwright_context = None
        self._playwright_browser_handle = None
        self._playwright_runtime = None
        self._playwright_warmed_mode = None

        for resource in (page, context, browser):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass
        if runtime is not None:
            try:
                runtime.stop()
            except Exception:
                pass

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


def _build_reference_attempts(shipment: ShipmentRef, booking_code: str, container_code: str) -> list[tuple[str, str]]:
    attempts: list[tuple[str, str]] = []
    if shipment.container_no:
        ref = _normalize_reference(shipment.container_no)
        if ref:
            attempts.append((ref, container_code))
    if shipment.booking_no:
        ref = _normalize_reference(shipment.booking_no)
        if ref:
            attempts.append((ref, booking_code))
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


def _status_from_page(*, html: str, source_url: str, eta_only_mode: bool) -> ShipmentStatus:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    normalized_text = text.lower()
    if "security check" in normalized_text or "just a moment" in normalized_text:
        raise ValueError("Hapag page blocked by security check")
    if "tracing by booking" not in normalized_text and "tracing by container" not in normalized_text:
        raise ValueError("Hapag tracking page did not render the expected result view")

    recent_moves = _extract_page_moves(soup)
    latest_move = recent_moves[-1] if recent_moves else None
    eta_time, eta_local_text = _derive_eta_from_page_moves(recent_moves)
    last_movement = _extract_page_last_movement(text)
    discovered_containers = extract_container_numbers(text)

    status_text = (latest_move.name if latest_move else None) or last_movement or _eta_status_text(eta_time)
    location = latest_move.location if latest_move else None
    event_time = latest_move.event_time if latest_move else None
    movement_details = _extract_page_transport_details(soup, latest_move)
    vessel_voyage = _extract_page_final_vessel_voyage(soup)

    if eta_only_mode:
        return ShipmentStatus(
            status_text=_eta_status_text(eta_time),
            eta_time=eta_time,
            eta_local_text=eta_local_text,
            latest_move=latest_move,
            recent_moves=recent_moves,
            discovered_containers=discovered_containers,
            raw_source=f"hapag-playwright:{source_url}",
            source_url=source_url,
            vessel_voyage=vessel_voyage,
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
        raw_source=f"hapag-playwright:{source_url}",
        source_url=source_url,
        movement_details=movement_details,
        vessel_voyage=vessel_voyage,
    )


def _extract_page_moves(soup: BeautifulSoup) -> list[MovementEvent]:
    for table in soup.find_all("table"):
        headers = [_clean_page_text(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        normalized = [header.lower() for header in headers]
        if not normalized:
            continue
        if "status" not in normalized:
            continue
        if not any("date" in header for header in normalized):
            continue

        status_idx = _header_index(normalized, "status")
        location_idx = _header_index(normalized, "place")
        if location_idx is None:
            location_idx = _header_index(normalized, "activity")
        date_idx = _header_index(normalized, "date")
        time_idx = _header_index(normalized, "time")
        transport_idx = _header_index(normalized, "transport")
        voyage_idx = _header_index(normalized, "voyage")

        moves: list[MovementEvent] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            values = [_clean_page_text(cell.get_text(" ", strip=True)) for cell in cells]
            status = _value_at(values, status_idx)
            if not status:
                continue
            date_text = _value_at(values, date_idx)
            time_text = _value_at(values, time_idx)
            event_time_local_text = " ".join(part for part in (date_text, time_text) if part).strip() or date_text
            detail_parts = [part for part in (_value_at(values, transport_idx), _value_at(values, voyage_idx)) if part]
            fallback_name = status
            if detail_parts:
                fallback_name = f"{status} ({' | '.join(detail_parts)})"

            moves.append(
                MovementEvent(
                    name=to_dcsa_movement_name(
                        event={"eventDescription": status},
                        fallback_name=fallback_name,
                    ),
                    location=_value_at(values, location_idx),
                    event_time=parse_event_time(event_time_local_text),
                    event_time_local_text=event_time_local_text or None,
                    event_state="actual" if row.find(["strong", "b"]) else "estimated",
                )
            )
        if moves:
            return moves
    return []


def _extract_page_last_movement(text: str) -> str | None:
    for line in [part.strip() for part in text.splitlines() if part.strip()]:
        if line.lower().startswith("the vessel "):
            return line
    return None


def _derive_eta_from_page_moves(moves: list[MovementEvent]) -> tuple[datetime | None, str | None]:
    arrival_candidates: list[MovementEvent] = []
    for move in moves:
        if move.event_time is None:
            continue
        if "arriv" in move.name.lower():
            arrival_candidates.append(move)
    if arrival_candidates:
        last_arrival = arrival_candidates[-1]
        return last_arrival.event_time, last_arrival.event_time_local_text
    for move in reversed(moves):
        if move.event_time is not None:
            return move.event_time, move.event_time_local_text
    return None, None


def _extract_page_transport_details(soup: BeautifulSoup, latest_move: MovementEvent | None) -> str | None:
    if latest_move is None:
        return None
    for table in soup.find_all("table"):
        headers = [_clean_page_text(th.get_text(" ", strip=True)).lower() for th in table.find_all("th")]
        if "status" not in headers or not any("transport" in header for header in headers):
            continue
        rows = table.find_all("tr")
        if not rows:
            continue
        last_cells = rows[-1].find_all("td")
        if not last_cells:
            continue
        values = [_clean_page_text(cell.get_text(" ", strip=True)) for cell in last_cells]
        transport = _value_at(values, _header_index(headers, "transport"))
        voyage = _value_at(values, _header_index(headers, "voyage"))
        parts = [part for part in (transport, voyage) if part]
        if parts:
            return " | ".join(parts)
    return None


def _extract_page_final_vessel_voyage(soup: BeautifulSoup) -> str | None:
    candidates: list[tuple[datetime | None, int, str]] = []
    for table in soup.find_all("table"):
        headers = [_clean_page_text(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        normalized = [header.lower() for header in headers]
        if "status" not in normalized or not any("transport" in header for header in normalized):
            continue

        status_idx = _header_index(normalized, "status")
        date_idx = _header_index(normalized, "date")
        time_idx = _header_index(normalized, "time")
        transport_idx = _header_index(normalized, "transport")
        voyage_idx = _header_index(normalized, "voyage")

        for idx, row in enumerate(table.find_all("tr")):
            cells = row.find_all("td")
            if not cells:
                continue
            values = [_clean_page_text(cell.get_text(" ", strip=True)) for cell in cells]
            status = _value_at(values, status_idx) or ""
            if not _hapag_final_vessel_event(status):
                continue
            parts = [part for part in (_value_at(values, transport_idx), _value_at(values, voyage_idx)) if part]
            if not parts:
                continue
            date_text = _value_at(values, date_idx)
            time_text = _value_at(values, time_idx)
            event_time_local_text = " ".join(part for part in (date_text, time_text) if part).strip() or date_text
            candidates.append((parse_event_time(event_time_local_text), idx, " ".join(parts)))

    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item[0] is not None,
            item[0].isoformat() if item[0] is not None else "",
            item[1],
        ),
    )[2]


def _hapag_final_vessel_event(value: str) -> bool:
    normalized = value.strip().lower()
    return "arriv" in normalized or "discharg" in normalized


def _header_index(headers: list[str], fragment: str) -> int | None:
    for idx, header in enumerate(headers):
        if fragment in header:
            return idx
    return None


def _value_at(values: list[str], idx: int | None) -> str | None:
    if idx is None:
        return None
    if idx < 0 or idx >= len(values):
        return None
    value = values[idx].strip()
    return value or None


def _clean_page_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _wait_for_hapag_result_page(
    page: Any,
    *,
    timeout_ms: int,
    challenge_timeout_seconds: int,
    reload_attempts: int,
    post_load_delay_seconds: float,
) -> None:
    deadline = time.monotonic() + max(1, challenge_timeout_seconds)
    reloads_used = 0
    while True:
        html = page.content()
        lowered = html.lower()
        if _hapag_result_view_ready(lowered):
            if post_load_delay_seconds > 0:
                page.wait_for_timeout(int(post_load_delay_seconds * 1000))
            return
        if time.monotonic() >= deadline:
            if _hapag_security_page(lowered) and reloads_used < max(0, reload_attempts):
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                _perform_hapag_interaction(page)
                reloads_used += 1
                deadline = time.monotonic() + max(1, challenge_timeout_seconds)
                continue
            if _hapag_security_page(lowered):
                raise ValueError("Hapag page blocked by security check")
            raise ValueError("Hapag tracking page did not render the expected result view")
        page.wait_for_timeout(1000)


def _hapag_result_view_ready(html: str) -> bool:
    return "tracing by booking" in html or "tracing by container" in html


def _hapag_security_page(html: str) -> bool:
    return "security check" in html or "just a moment" in html


def _wait_for_hapag_challenge_stage(
    page: Any,
    *,
    timeout_ms: int,
    challenge_timeout_seconds: int,
    reload_attempts: int,
) -> None:
    deadline = time.monotonic() + max(1, challenge_timeout_seconds)
    reloads_used = 0
    while True:
        html = page.content().lower()
        if not _hapag_security_page(html):
            return
        if time.monotonic() >= deadline:
            if reloads_used < max(0, reload_attempts):
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                _perform_hapag_interaction(page)
                reloads_used += 1
                deadline = time.monotonic() + max(1, challenge_timeout_seconds)
                continue
            # Keep the warmed session and let the target navigation try with whatever cookies we gained.
            return
        page.wait_for_timeout(1000)


def _perform_hapag_interaction(page: Any) -> None:
    try:
        page.mouse.move(220, 180)
        page.wait_for_timeout(250)
        page.mouse.move(480, 260)
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(250)
        page.mouse.wheel(0, -150)
    except Exception:
        return


def _should_reset_playwright_session(exc: Exception) -> bool:
    message = str(exc).lower()
    reset_markers = (
        "target page, context or browser has been closed",
        "browser has been closed",
        "context closed",
        "connection closed",
        "crash",
    )
    return any(marker in message for marker in reset_markers)


_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4]});
window.chrome = window.chrome || { runtime: {} };
"""


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
        state_hint = extract_event_state_hint(event)
        moves.append(
            MovementEvent(
                name=to_dcsa_movement_name(event=event, fallback_name=raw_name),
                location=location,
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
