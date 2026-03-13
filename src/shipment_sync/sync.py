import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import socket
from urllib.parse import urlparse

import requests

from shipment_sync.carriers.registry import build_carrier_registry
from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import Settings


@dataclass
class UpdatedShipment:
    task_id: str
    task_name: str
    shipping_line: str
    booking_no: str | None
    container_no: str | None
    status_text: str
    location: str | None
    event_time: datetime | None
    eta_time: datetime | None
    eta_local_text: str | None
    latest_move_name: str | None
    latest_move_location: str | None
    latest_move_time_local_text: str | None
    movement_details: str | None
    list_id: str
    list_name: str | None


@dataclass
class SyncStats:
    updated_items: list[UpdatedShipment]
    skipped: int
    total_candidates: int
    candidates_by_list: dict[str, int] = field(default_factory=dict)
    updated_by_list: dict[str, int] = field(default_factory=dict)


def run_sync(client: ClickUpClient) -> SyncStats:
    adapters = build_carrier_registry()
    all_shipments = client.list_shipments()

    updated_items: list[UpdatedShipment] = []
    skipped = 0
    candidates_by_list: dict[str, int] = {}
    updated_by_list: dict[str, int] = {}
    prefiltered_excluded_counts: dict[str, int] = {}
    prefiltered_not_allowed_counts: dict[str, int] = {}
    prefiltered_recent_counts: dict[str, int] = {}
    unsupported_line_counts: dict[str, int] = {}
    adapter_config_counts: dict[str, int] = {}
    progress_every = _int_env("SHIPMENT_PROGRESS_EVERY", default=10, min_value=1)
    supported_lines = set(adapters.keys())
    allowed_lines = set(client.settings.shipment_allowed_lines or [])
    excluded_lines = set(client.settings.shipment_excluded_lines or [])
    min_sync_interval_hours = max(0, client.settings.shipment_min_sync_interval_hours)
    now_utc = datetime.now(timezone.utc)
    eligible_shipments = []

    if min_sync_interval_hours > 0 and not client.settings.cf_status_last_checked:
        print(
            "SHIPMENT_MIN_SYNC_INTERVAL_HOURS is set but CLICKUP_CF_STATUS_LAST_CHECKED is not configured; "
            "recent-sync prefilter is disabled.",
            file=sys.stderr,
        )

    for shipment in all_shipments:
        line_name = shipment.shipping_line
        if line_name in excluded_lines:
            prefiltered_excluded_counts[line_name] = prefiltered_excluded_counts.get(line_name, 0) + 1
            continue
        if allowed_lines and line_name not in allowed_lines:
            prefiltered_not_allowed_counts[line_name] = prefiltered_not_allowed_counts.get(line_name, 0) + 1
            continue
        if min_sync_interval_hours > 0 and shipment.last_checked_at is not None:
            age_seconds = (now_utc - shipment.last_checked_at).total_seconds()
            if age_seconds < min_sync_interval_hours * 3600:
                prefiltered_recent_counts[line_name] = prefiltered_recent_counts.get(line_name, 0) + 1
                continue
        if client.settings.shipment_skip_unsupported_lines and line_name not in supported_lines:
            unsupported_line_counts[line_name] = unsupported_line_counts.get(line_name, 0) + 1
            continue
        eligible_shipments.append(shipment)

    total_candidates = len(eligible_shipments)
    preflight_failures: dict[str, str] = {}
    if client.settings.shipment_preflight_enabled and eligible_shipments:
        active_supported_lines = {s.shipping_line for s in eligible_shipments if s.shipping_line in supported_lines}
        preflight_failures = _preflight_supported_lines(active_supported_lines, client.settings)
        if preflight_failures:
            failed_counts: dict[str, int] = {}
            kept_shipments = []
            for shipment in eligible_shipments:
                reason = preflight_failures.get(shipment.shipping_line)
                if reason:
                    failed_counts[shipment.shipping_line] = failed_counts.get(shipment.shipping_line, 0) + 1
                    skipped += 1
                    continue
                kept_shipments.append(shipment)
            eligible_shipments = kept_shipments
            print("Prefiltered due to carrier preflight failure:", file=sys.stderr)
            for line_name, count in sorted(failed_counts.items(), key=lambda item: (-item[1], item[0])):
                print(f"- {line_name}: {count} task(s) | {preflight_failures[line_name]}", file=sys.stderr)

    total_eligible = len(eligible_shipments)
    if total_eligible:
        print(f"Starting shipment sync for {total_eligible} eligible task(s)...", file=sys.stderr)

    for idx, shipment in enumerate(eligible_shipments, start=1):
        if idx == 1 or idx == total_eligible or (progress_every > 0 and idx % progress_every == 0):
            print(
                f"Progress: {idx}/{total_eligible} | task={shipment.task_id} | line={shipment.shipping_line}",
                file=sys.stderr,
            )

        list_label = _list_label(shipment.list_name, shipment.list_id)
        candidates_by_list[list_label] = candidates_by_list.get(list_label, 0) + 1

        adapter = adapters.get(shipment.shipping_line)
        if not adapter:
            unsupported_line_counts[shipment.shipping_line] = unsupported_line_counts.get(shipment.shipping_line, 0) + 1
            skipped += 1
            continue

        try:
            status = adapter.fetch_status(shipment)
            client.update_shipment_status(shipment, status)
            updated_items.append(
                UpdatedShipment(
                    task_id=shipment.task_id,
                    task_name=shipment.task_name,
                    shipping_line=shipment.shipping_line,
                    booking_no=shipment.booking_no,
                    container_no=shipment.container_no,
                    status_text=status.status_text,
                    location=status.location,
                    event_time=status.event_time,
                    eta_time=status.eta_time,
                    eta_local_text=status.eta_local_text,
                    latest_move_name=status.latest_move.name if status.latest_move else None,
                    latest_move_location=status.latest_move.location if status.latest_move else None,
                    latest_move_time_local_text=status.latest_move.event_time_local_text if status.latest_move else None,
                    movement_details=status.movement_details,
                    list_id=shipment.list_id,
                    list_name=shipment.list_name,
                )
            )
            updated_by_list[list_label] = updated_by_list.get(list_label, 0) + 1
        except Exception as exc:
            message = str(exc)
            if "adapter not configured" in message.lower():
                key = f"{shipment.shipping_line}: {message}"
                adapter_config_counts[key] = adapter_config_counts.get(key, 0) + 1
                skipped += 1
                continue
            print(
                f"Skipped task {shipment.task_id} ({shipment.task_name}): {exc}",
                file=sys.stderr,
            )
            skipped += 1

    if prefiltered_excluded_counts:
        print("Prefiltered excluded shipping lines:", file=sys.stderr)
        for line_name, count in sorted(prefiltered_excluded_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"- {line_name}: {count} task(s)", file=sys.stderr)

    if prefiltered_not_allowed_counts:
        print("Prefiltered non-allowlisted shipping lines:", file=sys.stderr)
        for line_name, count in sorted(prefiltered_not_allowed_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"- {line_name}: {count} task(s)", file=sys.stderr)

    if prefiltered_recent_counts:
        print(
            f"Prefiltered recently synced tasks (< {min_sync_interval_hours}h):",
            file=sys.stderr,
        )
        for line_name, count in sorted(prefiltered_recent_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"- {line_name}: {count} task(s)", file=sys.stderr)

    if unsupported_line_counts:
        print("Skipped unsupported shipping lines:", file=sys.stderr)
        for line_name, count in sorted(unsupported_line_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"- {line_name}: {count} task(s)", file=sys.stderr)

    if adapter_config_counts:
        print("Skipped due to adapter configuration:", file=sys.stderr)
        for reason, count in sorted(adapter_config_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"- {reason} [{count} task(s)]", file=sys.stderr)

    return SyncStats(
        updated_items=updated_items,
        skipped=skipped,
        total_candidates=total_candidates,
        candidates_by_list=candidates_by_list,
        updated_by_list=updated_by_list,
    )


def _list_label(list_name: str | None, list_id: str) -> str:
    if list_name and list_name.strip():
        return f"{list_name.strip()} ({list_id})"
    return list_id


def _preflight_supported_lines(lines: set[str], settings: Settings) -> dict[str, str]:
    failures: dict[str, str] = {}
    session = requests.Session()
    timeout = max(1, settings.shipment_preflight_timeout_seconds)
    for line_name in sorted(lines):
        if line_name in {"cosco", "cosco shipping", "cosco shipping line", "cosco shipping lines"}:
            ok, reason = _probe_cosco_tracking_access(timeout, session)
            if not ok:
                failures[line_name] = reason
            continue
        if line_name in {"cma cgm", "cma-cgm", "cma - cgm"}:
            ok, reason = _probe_cma_cgm_tracking_access(timeout, session)
            if not ok:
                failures[line_name] = reason
            continue
        hosts = _preflight_hosts_for_line(line_name)
        if not hosts:
            continue
        for host in hosts:
            ok, reason = _probe_host(host, timeout, session)
            if not ok:
                failures[line_name] = f"{host}: {reason}"
                break
    return failures


def _preflight_hosts_for_line(line_name: str) -> list[str]:
    normalized = (line_name or "").strip().lower()
    hosts: list[str] = []

    if normalized in {"one", "ocean network express"}:
        one_use_edh = _env_bool("ONE_USE_EDH_API", True)
        one_template = os.getenv("ONE_TRACKING_URL_TEMPLATE", "").strip()
        one_api_url = os.getenv("ONE_TRACKING_API_URL", "").strip()
        if one_use_edh:
            _append_host(hosts, os.getenv("ONE_EDH_BASE_URL", "https://ecomm.one-line.com/api/v1/edh"))
        if one_api_url:
            _append_host(hosts, one_api_url)
        if one_template:
            _append_host(hosts, one_template)
        if not hosts:
            _append_host(hosts, "https://ecomm.one-line.com/one-ecom/manage-shipment/cargo-tracking")
        return hosts

    if normalized in {"maersk", "maersk line", "a.p. moller - maersk"}:
        api_mode = os.getenv("MAERSK_API_MODE", "auto").strip().lower()
        consumer_key = os.getenv("MAERSK_CONSUMER_KEY", "").strip()
        bearer_token = os.getenv("MAERSK_BEARER_TOKEN", "").strip()
        oauth_token_url = os.getenv("MAERSK_OAUTH_TOKEN_URL", "").strip()
        web_fallback_enabled = _env_bool("MAERSK_WEB_FALLBACK_ON_API_ERROR", True)
        maersk_template = os.getenv("MAERSK_TRACKING_URL_TEMPLATE", "").strip()
        maersk_api_url = os.getenv("MAERSK_TRACKING_API_URL", "").strip()
        use_events_api = api_mode == "events" or (api_mode != "legacy" and bool(consumer_key or bearer_token or oauth_token_url))

        # If API URL is configured, preflight API/OAuth first and only include web host when fallback is enabled.
        if maersk_api_url:
            _append_host(hosts, maersk_api_url)
            if use_events_api and not bearer_token:
                _append_host(hosts, oauth_token_url or "https://api.maersk.com/oauth2/access_token")
            if web_fallback_enabled:
                _append_host(hosts, maersk_template or "https://www.maersk.com/tracking/{reference}")
            return hosts

        # No API URL configured: web mode only.
        _append_host(hosts, maersk_template or "https://www.maersk.com/tracking/{reference}")
        return hosts

    if normalized in {"hapag lloyd", "hapag-lloyd", "hapag lloyd ag"}:
        hapag_api_url = os.getenv("HAPAG_TRACKING_API_URL", "").strip()
        hapag_template = os.getenv("HAPAG_TRACKING_URL_TEMPLATE", "").strip()
        hapag_page = os.getenv(
            "HAPAG_TRACKING_PAGE_URL_TEMPLATE",
            "https://www.hapag-lloyd.com/en/online-business/track/track-by-container-solution.html",
        ).strip()
        bearer_token = os.getenv("HAPAG_BEARER_TOKEN", "").strip()
        oauth_token_url = os.getenv("HAPAG_OAUTH_TOKEN_URL", "").strip()

        if hapag_api_url:
            _append_host(hosts, hapag_api_url)
            if not bearer_token and oauth_token_url:
                _append_host(hosts, oauth_token_url)
        elif hapag_template:
            _append_host(hosts, hapag_template)
        else:
            _append_host(hosts, hapag_page)
        return hosts

    if normalized in {"cosco", "cosco shipping", "cosco shipping line", "cosco shipping lines"}:
        cosco_mode = os.getenv("COSCO_MODE", "cop").strip().lower() or "cop"
        cosco_cop_base = os.getenv("COSCO_COP_BASE_URL", "https://api-pp.lines.coscoshipping.com").strip()
        cosco_api_key = os.getenv("COSCO_COP_API_KEY", "").strip()
        cosco_secret = os.getenv("COSCO_COP_SECRET_KEY", "").strip()
        cosco_has_cop_creds = bool(cosco_api_key and cosco_secret)
        cosco_use_legacy_api = _env_bool("COSCO_USE_API", True)
        cosco_legacy_api_base = os.getenv("COSCO_TRACKING_API_BASE_URL", "").strip()
        cosco_template = os.getenv("COSCO_TRACKING_URL_TEMPLATE", "").strip()

        if cosco_mode == "cop":
            _append_host(hosts, cosco_cop_base)
            return hosts

        if cosco_mode == "auto" and cosco_has_cop_creds:
            _append_host(hosts, cosco_cop_base)
            return hosts

        if cosco_use_legacy_api and cosco_legacy_api_base:
            _append_host(hosts, cosco_legacy_api_base)
        elif cosco_template:
            _append_host(hosts, cosco_template)
        else:
            _append_host(hosts, "https://elines.coscoshipping.com/ebusiness/cargoTracking")
        return hosts

    if normalized in {"cma cgm", "cma-cgm", "cma - cgm"}:
        cma_api_url = os.getenv("CMA_CGM_TRACKING_API_URL", "").strip()
        cma_template = os.getenv("CMA_CGM_TRACKING_URL_TEMPLATE", "").strip()
        if cma_api_url:
            _append_host(hosts, cma_api_url)
        elif cma_template:
            _append_host(hosts, cma_template)
        else:
            _append_host(hosts, "https://www.cma-cgm.com/ebusiness/tracking/detail/MSCU1234567")
        return hosts

    if normalized in {"msc", "msc shipping line", "mediterranean shipping company"}:
        use_playwright = _env_bool("MSC_USE_PLAYWRIGHT", True)
        playwright_required = _env_bool("MSC_PLAYWRIGHT_REQUIRED", False)
        if use_playwright:
            _append_host(
                hosts,
                os.getenv("MSC_PLAYWRIGHT_TRACKING_URL", "https://www.msc.com/en/track-a-shipment"),
            )
            _append_host(
                hosts,
                os.getenv("MSC_PLAYWRIGHT_API_ENDPOINT", "https://www.msc.com/api/feature/tools/TrackingInfo"),
            )
            if playwright_required:
                return hosts

        fallback_hosts = _hosts_from_generic_prefix(
            env_prefix="MSC",
            default_template="https://www.msc.com/en/track-a-shipment",
        )
        for host in fallback_hosts:
            if host not in hosts:
                hosts.append(host)
        return hosts

    if normalized in {"pil", "pacific international lines"}:
        return _hosts_from_generic_prefix(
            env_prefix="PIL",
            default_template="https://www.pilship.com/en-our-track-and-trace-p.html",
        )

    if normalized in {"evergreen", "evergreen line", "evergreen marine"}:
        return _hosts_from_generic_prefix(
            env_prefix="EVERGREEN",
            default_template="https://www.evergreen-line.com/",
        )

    if normalized in {"wan hai", "wan hai lines"}:
        return _hosts_from_generic_prefix(
            env_prefix="WAN_HAI",
            default_template="https://www.wanhai.com/views/cargoTrack/CargoTrack.xhtml",
        )

    if normalized in {"oocl", "orient overseas container line"}:
        return _hosts_from_generic_prefix(
            env_prefix="OOCL",
            default_template="https://www.oocl.com/eng/ourservices/eservices/cargotracking/Pages/cargotracking.aspx",
        )

    return hosts


def _append_host(hosts: list[str], url_value: str) -> None:
    host = _host_from_url(url_value)
    if host and host not in hosts:
        hosts.append(host)


def _hosts_from_generic_prefix(*, env_prefix: str, default_template: str) -> list[str]:
    hosts: list[str] = []
    api_url = os.getenv(f"{env_prefix}_TRACKING_API_URL", "").strip()
    url_template = os.getenv(f"{env_prefix}_TRACKING_URL_TEMPLATE", "").strip()
    if api_url:
        _append_host(hosts, api_url)
    elif url_template:
        _append_host(hosts, url_template)
    else:
        _append_host(hosts, default_template)
    return hosts


def _host_from_url(url_value: str | None) -> str | None:
    if not url_value:
        return None
    candidate = url_value.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    return parsed.hostname


def _probe_host(host: str, timeout_seconds: int, session: requests.Session) -> tuple[bool, str]:
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return False, f"DNS resolution failed ({exc})"

    try:
        response = session.get(f"https://{host}", timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException as exc:
        return False, f"HTTPS probe failed ({exc})"
    return True, f"reachable (HTTP {response.status_code})"


def _probe_cosco_tracking_access(timeout_seconds: int, session: requests.Session) -> tuple[bool, str]:
    cosco_mode = os.getenv("COSCO_MODE", "cop").strip().lower() or "cop"
    cosco_api_key = os.getenv("COSCO_COP_API_KEY", "").strip()
    cosco_secret = os.getenv("COSCO_COP_SECRET_KEY", "").strip()
    has_cop_creds = bool(cosco_api_key and cosco_secret)

    if cosco_mode == "cop":
        if not has_cop_creds:
            return False, "COSCO COP not configured (set COSCO_COP_API_KEY and COSCO_COP_SECRET_KEY)"
        host = _host_from_url(os.getenv("COSCO_COP_BASE_URL", "https://api-pp.lines.coscoshipping.com"))
        if not host:
            return False, "COSCO COP base URL is invalid"
        ok, reason = _probe_host(host, timeout_seconds, session)
        if not ok:
            return False, f"COSCO COP host check failed ({reason})"
        return True, f"COSCO COP host reachable ({reason})"

    if cosco_mode == "auto" and has_cop_creds:
        host = _host_from_url(os.getenv("COSCO_COP_BASE_URL", "https://api-pp.lines.coscoshipping.com"))
        if host:
            ok, reason = _probe_host(host, timeout_seconds, session)
            if ok:
                return True, f"COSCO COP host reachable ({reason})"

    url = "https://elines.coscoshipping.com/ebusiness/cargoTracking?trackingType=CONTAINER&number=MSCU1234567"
    try:
        response = session.get(url, timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException as exc:
        return False, f"COSCO probe failed ({exc})"

    body = (response.text or "").lower()
    blocked = (
        "this page can't be displayed" in body
        or "页面无法显示" in response.text
        or "support@coscon.com" in body
        or "<title>error</title>" in body
    )
    if blocked:
        return False, "COSCO legacy tracking blocked by anti-bot (recommend COSCO COP API mode)"
    return True, f"COSCO tracking reachable (HTTP {response.status_code})"


def _probe_cma_cgm_tracking_access(timeout_seconds: int, session: requests.Session) -> tuple[bool, str]:
    cma_api_url = os.getenv("CMA_CGM_TRACKING_API_URL", "").strip()
    cma_api_base = os.getenv("CMA_CGM_API_BASE_URL", "").strip()
    cma_api_method = os.getenv("CMA_CGM_API_METHOD", "").strip()
    cma_api_method_path = os.getenv("CMA_CGM_API_METHOD_PATH", "").strip()
    api_mode_requested = bool(cma_api_method or cma_api_method_path or cma_api_base)

    if api_mode_requested and not cma_api_url and not cma_api_base:
        return (
            False,
            "CMA-CGM API mode configured but endpoint missing "
            "(set CMA_CGM_TRACKING_API_URL or CMA_CGM_API_BASE_URL)",
        )

    if cma_api_url or cma_api_base:
        if cma_api_url:
            endpoint = cma_api_url
        else:
            method_path = cma_api_method_path or _cma_method_name_to_path(cma_api_method)
            if method_path:
                normalized_method = method_path if method_path.startswith("/") else f"/{method_path}"
                endpoint = f"{cma_api_base.rstrip('/')}{normalized_method}"
            else:
                endpoint = cma_api_base

        host = _host_from_url(endpoint)
        if not host:
            return False, "CMA-CGM API URL is invalid"

        ok, reason = _probe_host(host, timeout_seconds, session)
        if not ok:
            return False, f"CMA-CGM API host check failed ({reason})"
        return True, f"CMA-CGM API host reachable ({reason})"

    url = "https://www.cma-cgm.com/ebusiness/tracking/detail/MSCU1234567"
    try:
        response = session.get(url, timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException as exc:
        return False, f"CMA-CGM probe failed ({exc})"

    body = (response.text or "").lower()
    blocked = (
        "please enable js and disable any ad blocker" in body
        or "captcha" in body
        or "access denied" in body
        or "<title>cma-cgm.com</title>" in body
    )
    if blocked:
        return False, "CMA-CGM anti-bot challenge page returned"
    return True, f"CMA-CGM tracking reachable (HTTP {response.status_code})"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(key: str, default: int, *, min_value: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        parsed = int(raw.strip())
    except Exception:
        return default
    return parsed if parsed >= min_value else default


def _cma_method_name_to_path(method_name: str) -> str:
    normalized = (method_name or "").strip().lower()
    if not normalized:
        return ""
    if normalized == "searchmoveoncommercialcycle":
        return "/events"
    if normalized == "getmoveoncommercialcycle":
        return "/events/{trackingReference}"
    return method_name
