from __future__ import annotations

from datetime import datetime
import os
import secrets
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
import requests
from pydantic import BaseModel
import uvicorn

from shipment_sync.ap_config import AccountsPayableSettings
from shipment_sync.ap_models import AccountsPayableInvoice
from shipment_sync.audit import AuditRun, SyncAuditStore
from shipment_sync.clickup_ap_client import ClickUpAccountsPayableClient
from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.clickup_pricing_client import ClickUpPricingClient
from shipment_sync.config import Settings
from shipment_sync.document_routes import register_document_routes
from shipment_sync.models import ShipmentRef
from shipment_sync.one_booking_client import OneBookingClient
from shipment_sync.one_booking_config import OneBookingConfigReport, OneBookingSettings
from shipment_sync.one_booking_models import OneBookingResult
from shipment_sync.pricing_sync import (
    AmbiguousQuoteMatchError,
    find_quote_for_shipment,
    run_bulk_pricing_sync,
    sync_pricing_pair,
)
from shipment_sync.pricing_sync_config import PricingSyncSettings
from shipment_sync.track_trace_config import ApiTriggerSettings, TrackTraceConfigReport, inspect_track_trace_env
from shipment_sync.sync import SyncStats, UpdatedShipment, run_sync


class HealthResponse(BaseModel):
    status: str
    configured: bool
    detail: str | None = None


class ShipmentRefResponse(BaseModel):
    task_id: str
    task_name: str
    shipping_line: str
    booking_no: str | None
    container_no: str | None
    list_id: str
    list_name: str | None
    last_checked_at: str | None


class ListShipmentsResponse(BaseModel):
    count: int
    items: list[ShipmentRefResponse]


class UpdatedShipmentResponse(BaseModel):
    task_id: str
    task_name: str
    shipping_line: str
    booking_no: str | None
    container_no: str | None
    status_text: str
    location: str | None
    event_time: str | None
    eta_time: str | None
    eta_local_text: str | None
    latest_move_name: str | None
    latest_move_location: str | None
    latest_move_time_local_text: str | None
    movement_details: str | None
    vessel_voyage: str | None
    list_id: str
    list_name: str | None


class SyncResponse(BaseModel):
    total_candidates: int
    updated: int
    unchanged: int
    skipped: int
    candidates_by_list: dict[str, int]
    updated_by_list: dict[str, int]
    unchanged_by_list: dict[str, int]
    updated_items: list[UpdatedShipmentResponse]


class AccountsPayableInvoiceResponse(BaseModel):
    task_id: str
    task_name: str
    task_url: str | None
    invoice_number: str | None
    vendor: str | None
    amount: str | None
    currency: str | None
    status: str | None
    due_date: str | None
    list_id: str
    list_name: str | None
    is_closed: bool
    is_archived: bool


class ListAccountsPayableInvoicesResponse(BaseModel):
    total_matches: int
    returned: int
    has_invoices: bool
    items: list[AccountsPayableInvoiceResponse]


class PricingFieldUpdateResponse(BaseModel):
    field_id: str
    field_name: str
    value: Any
    source_value: Any
    existing_value: Any
    transform: str | None


class PricingSyncResultResponse(BaseModel):
    shipment_task_id: str | None
    shipment_custom_id: str | None
    shipment_name: str | None
    quote_task_id: str | None
    quote_custom_id: str | None
    quote_name: str | None
    match_selector: str | None = None
    match_value: str | None = None
    dry_run: bool
    applied_updates: int
    updates: list[PricingFieldUpdateResponse]
    skip_reason: str | None = None


class PricingSyncResponse(BaseModel):
    mode: str
    shipments_matched: int
    shipments_updated: int
    shipments_skipped: int
    results: list[PricingSyncResultResponse]


class PricingSyncRequest(BaseModel):
    shipment: str | None = None
    quote: str | None = None
    sync_linked_shipments: bool = False
    overwrite_existing: bool = False
    dry_run: bool = False


class OneBookingRequestBody(BaseModel):
    payload: dict[str, Any]


class OneBookingResponse(BaseModel):
    status_text: str
    booking_request_no: str | None
    booking_no: str | None
    confirmation_no: str | None
    reference: str | None
    reference_type: str | None
    source_url: str | None
    raw_source: str | None
    raw_response: dict[str, Any]


class OneBookingRequirementsResponse(BaseModel):
    configured: bool
    live_ready: bool
    required_items: list[str]
    missing_required_items: list[str]
    recommended_items: list[str]
    missing_recommended_items: list[str]
    notes: list[str]


class TrackTraceRequirementsResponse(BaseModel):
    configured: bool
    live_ready: bool
    required_items: list[str]
    missing_required_items: list[str]
    recommended_items: list[str]
    missing_recommended_items: list[str]
    notes: list[str]


class TrackTraceRunRequest(BaseModel):
    dry_run: bool = False
    source: str | None = None
    payload: dict[str, Any] | None = None


class AuditRunResponse(BaseModel):
    id: str
    source: str | None
    started_at: str
    finished_at: str | None
    status: str
    host: str | None
    allowed_lines: list[str]
    total_candidates: int | None
    updated: int | None
    unchanged: int | None
    skipped: int | None
    error: str | None


class AuditRunsResponse(BaseModel):
    count: int
    items: list[AuditRunResponse]


class AuditRunDetailResponse(BaseModel):
    run: dict[str, Any]
    task_events: list[dict[str, Any]]
    log_entries: list[dict[str, Any]]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Shipment Sync API",
        version="0.1.0",
        description="HTTP API for previewing ClickUp shipment tasks and running shipment sync jobs.",
    )
    register_document_routes(app)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"name": "shipment-sync-api", "docs_url": "/docs", "health_url": "/health"}

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        load_dotenv()
        report = inspect_track_trace_env()
        detail_parts: list[str] = []
        if report.missing_required_items:
            detail_parts.append("Missing required: " + ", ".join(report.missing_required_items))
        if report.missing_recommended_items:
            detail_parts.append("Missing recommended: " + ", ".join(report.missing_recommended_items))
        detail = " | ".join(detail_parts) if detail_parts else None
        return HealthResponse(status="ok", configured=report.configured, detail=detail)

    @app.get("/track-trace/requirements", response_model=TrackTraceRequirementsResponse)
    def track_trace_requirements() -> TrackTraceRequirementsResponse:
        load_dotenv()
        return _serialize_track_trace_config_report(inspect_track_trace_env())

    @app.get("/shipments", response_model=ListShipmentsResponse)
    def list_shipments(
        client: Annotated[ClickUpClient, Depends(_get_client)],
        _: None = Depends(_require_operator_auth),
    ) -> ListShipmentsResponse:
        try:
            shipments = client.list_shipments()
        except requests.RequestException as exc:
            raise _service_error(exc) from exc

        return ListShipmentsResponse(
            count=len(shipments),
            items=[_serialize_shipment_ref(item) for item in shipments],
        )

    @app.post("/sync", response_model=SyncResponse)
    def sync_shipments(
        client: Annotated[ClickUpClient, Depends(_get_client)],
        _: None = Depends(_require_operator_auth),
    ) -> SyncResponse:
        try:
            stats = run_sync(client)
        except requests.RequestException as exc:
            raise _service_error(exc) from exc

        return _serialize_sync_stats(stats)

    @app.post("/track-trace/run", response_model=SyncResponse)
    def run_track_trace_now(
        body: TrackTraceRunRequest,
        client: Annotated[ClickUpClient, Depends(_get_client)],
        _: None = Depends(_require_operator_auth),
    ) -> SyncResponse:
        if body.dry_run:
            raise HTTPException(status_code=400, detail="dry_run is not supported on the production trigger endpoint")
        try:
            stats = run_sync(client)
        except requests.RequestException as exc:
            raise _service_error(exc) from exc
        return _serialize_sync_stats(stats)

    @app.post("/webhooks/clickup/track-trace", response_model=SyncResponse)
    def clickup_track_trace_webhook(
        client: Annotated[ClickUpClient, Depends(_get_client)],
        _: None = Depends(_require_operator_auth),
    ) -> SyncResponse:
        try:
            stats = run_sync(client)
        except requests.RequestException as exc:
            raise _service_error(exc) from exc
        return _serialize_sync_stats(stats)

    @app.get("/audit/runs", response_model=AuditRunsResponse)
    def list_audit_runs(
        store: Annotated[SyncAuditStore, Depends(_get_audit_store)],
        _: None = Depends(_require_operator_auth),
        limit: int = 50,
    ) -> AuditRunsResponse:
        runs = store.list_runs(limit=limit)
        return AuditRunsResponse(
            count=len(runs),
            items=[_serialize_audit_run(run) for run in runs],
        )

    @app.get("/audit/runs/{run_id}", response_model=AuditRunDetailResponse)
    def get_audit_run(
        run_id: str,
        store: Annotated[SyncAuditStore, Depends(_get_audit_store)],
        _: None = Depends(_require_operator_auth),
        task_limit: int = 500,
        log_limit: int = 500,
    ) -> AuditRunDetailResponse:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Audit run not found")
        return AuditRunDetailResponse(
            run=run,
            task_events=store.list_task_events(run_id=run_id, limit=task_limit),
            log_entries=store.list_log_entries(run_id=run_id, limit=log_limit),
        )

    @app.get("/ap/health", response_model=HealthResponse)
    def ap_health() -> HealthResponse:
        load_dotenv()
        try:
            AccountsPayableSettings.from_env()
        except ValueError as exc:
            return HealthResponse(status="ok", configured=False, detail=str(exc))
        return HealthResponse(status="ok", configured=True)

    @app.get("/ap/invoices", response_model=ListAccountsPayableInvoicesResponse)
    def list_ap_invoices(
        client: Annotated[ClickUpAccountsPayableClient, Depends(_get_ap_client)],
        _: None = Depends(_require_operator_auth),
        query: str | None = None,
        limit: int = 100,
    ) -> ListAccountsPayableInvoicesResponse:
        if limit < 0:
            raise HTTPException(status_code=400, detail="limit must be >= 0")
        try:
            invoices = client.list_invoices(query=query)
        except requests.RequestException as exc:
            raise _service_error(exc) from exc

        visible = invoices if limit == 0 else invoices[:limit]
        return ListAccountsPayableInvoicesResponse(
            total_matches=len(invoices),
            returned=len(visible),
            has_invoices=bool(invoices),
            items=[_serialize_ap_invoice(item) for item in visible],
        )

    @app.get("/pricing/health", response_model=HealthResponse)
    def pricing_health() -> HealthResponse:
        load_dotenv()
        try:
            settings = PricingSyncSettings.from_env()
        except ValueError as exc:
            return HealthResponse(status="ok", configured=False, detail=str(exc))

        detail_parts: list[str] = []
        if not settings.clickup_shipment_list_ids:
            detail_parts.append("Bulk sync missing CLICKUP_LIST_ID or CLICKUP_LIST_IDS")
        if not settings.clickup_pricing_list_ids:
            detail_parts.append("Bulk sync missing CLICKUP_PRICING_LIST_ID or CLICKUP_PRICING_LIST_IDS")
        detail = " | ".join(detail_parts) if detail_parts else None
        return HealthResponse(status="ok", configured=True, detail=detail)

    @app.post("/pricing/sync", response_model=PricingSyncResponse)
    def sync_pricing(
        body: PricingSyncRequest,
        settings: Annotated[PricingSyncSettings, Depends(_get_pricing_settings)],
        client: Annotated[ClickUpPricingClient, Depends(_get_pricing_client)],
        _: None = Depends(_require_operator_auth),
    ) -> PricingSyncResponse:
        overwrite_existing = body.overwrite_existing or not settings.clickup_pricing_only_empty_targets

        if body.sync_linked_shipments:
            if body.shipment or body.quote:
                raise HTTPException(
                    status_code=400,
                    detail="Use either sync_linked_shipments or an explicit shipment/quote pair.",
                )
            try:
                summary = run_bulk_pricing_sync(
                    client,
                    settings,
                    dry_run=body.dry_run,
                    overwrite_existing=overwrite_existing,
                )
            except requests.RequestException as exc:
                raise _service_error(exc) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            return PricingSyncResponse(
                mode="bulk",
                shipments_matched=summary["shipments_matched"],
                shipments_updated=summary["shipments_updated"],
                shipments_skipped=summary["shipments_skipped"],
                results=[_serialize_pricing_sync_result(item) for item in summary["results"]],
            )

        if not body.shipment:
            raise HTTPException(
                status_code=400,
                detail="Provide shipment, optionally quote, or set sync_linked_shipments=true.",
            )

        try:
            shipment_task = client.get_task(body.shipment)
            matched_on: str | None = None
            matched_value: str | None = None
            if body.quote:
                quote_task = client.get_task(body.quote)
            else:
                quote_task, matched_on, matched_value = find_quote_for_shipment(
                    client,
                    settings,
                    shipment_task=shipment_task,
                )
                if quote_task is None:
                    if matched_on and matched_value is None:
                        raise HTTPException(status_code=404, detail=f"Could not discover quote via {matched_on}.")
                    raise HTTPException(status_code=404, detail="Could not discover quote for shipment.")
            result = sync_pricing_pair(
                client,
                settings,
                shipment_task=shipment_task,
                quote_task=quote_task,
                dry_run=body.dry_run,
                overwrite_existing=overwrite_existing,
            )
            if not body.quote:
                result["match_selector"] = matched_on
                result["match_value"] = matched_value
        except requests.RequestException as exc:
            raise _service_error(exc) from exc
        except AmbiguousQuoteMatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return PricingSyncResponse(
            mode="pair",
            shipments_matched=1,
            shipments_updated=1 if result["applied_updates"] > 0 else 0,
            shipments_skipped=0,
            results=[_serialize_pricing_sync_result(result)],
        )

    @app.get("/one/health", response_model=HealthResponse)
    def one_booking_health() -> HealthResponse:
        load_dotenv()
        report = OneBookingSettings.inspect_env()
        detail_parts: list[str] = []
        if report.missing_required_items:
            detail_parts.append("Missing required: " + ", ".join(report.missing_required_items))
        if report.missing_recommended_items:
            detail_parts.append("Missing recommended for live calls: " + ", ".join(report.missing_recommended_items))
        detail = " | ".join(detail_parts) if detail_parts else None
        return HealthResponse(status="ok", configured=report.configured, detail=detail)

    @app.get("/one/requirements", response_model=OneBookingRequirementsResponse)
    def one_booking_requirements() -> OneBookingRequirementsResponse:
        load_dotenv()
        report = OneBookingSettings.inspect_env()
        return _serialize_one_booking_config_report(report)

    @app.post("/one/bookings/request", response_model=OneBookingResponse)
    def submit_one_booking_request(
        body: OneBookingRequestBody,
        client: Annotated[OneBookingClient, Depends(_get_one_booking_client)],
        _: None = Depends(_require_operator_auth),
    ) -> OneBookingResponse:
        try:
            result = client.submit_booking_request(body.payload)
        except requests.RequestException as exc:
            raise _service_error(exc) from exc

        return _serialize_one_booking_result(result)

    @app.get("/one/bookings/confirmation", response_model=OneBookingResponse)
    def get_one_booking_confirmation(
        reference: str,
        reference_type: str = "booking_request",
        client: OneBookingClient = Depends(_get_one_booking_client),
        _: None = Depends(_require_operator_auth),
    ) -> OneBookingResponse:
        if not reference.strip():
            raise HTTPException(status_code=400, detail="reference is required")

        try:
            result = client.fetch_booking_confirmation(reference=reference.strip(), reference_type=reference_type.strip())
        except requests.RequestException as exc:
            raise _service_error(exc) from exc

        return _serialize_one_booking_result(result)

    return app


def main() -> None:
    host = os.getenv("SHIPMENT_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw_port = os.getenv("SHIPMENT_API_PORT") or os.getenv("PORT") or "8000"
    try:
        port = int(raw_port)
    except ValueError:
        port = 8000
    uvicorn.run("shipment_sync.api:create_app", factory=True, host=host, port=port)


def _get_client() -> ClickUpClient:
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ClickUpClient(settings)


def _get_ap_client() -> ClickUpAccountsPayableClient:
    load_dotenv()
    try:
        settings = AccountsPayableSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ClickUpAccountsPayableClient(settings)


def _get_pricing_settings() -> PricingSyncSettings:
    load_dotenv()
    try:
        return PricingSyncSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _get_pricing_client(
    settings: Annotated[PricingSyncSettings, Depends(_get_pricing_settings)],
) -> ClickUpPricingClient:
    return ClickUpPricingClient(settings)


def _get_one_booking_client() -> OneBookingClient:
    load_dotenv()
    try:
        settings = OneBookingSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OneBookingClient(settings)


def _get_audit_store() -> SyncAuditStore:
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not settings.shipment_audit_db_path:
        raise HTTPException(status_code=503, detail="SHIPMENT_AUDIT_DB_PATH is not configured")
    try:
        return SyncAuditStore(settings.shipment_audit_db_path)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Audit database unavailable: {exc}") from exc


def _require_operator_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_trigger_token: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> None:
    load_dotenv()
    settings = ApiTriggerSettings.from_env()
    expected = settings.trigger_token
    if not expected:
        raise HTTPException(status_code=503, detail="SHIPMENT_API_TRIGGER_TOKEN is not configured")

    provided = _extract_presented_token(
        authorization=authorization,
        x_trigger_token=x_trigger_token,
        query_token=token,
        allow_query_token=settings.allow_query_token,
    )
    if not provided:
        raise HTTPException(status_code=401, detail="Missing trigger authentication token")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid trigger authentication token")


def _service_error(exc: requests.RequestException) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Upstream service request failed: {exc}")


def _serialize_shipment_ref(item: ShipmentRef) -> ShipmentRefResponse:
    return ShipmentRefResponse(
        task_id=item.task_id,
        task_name=item.task_name,
        shipping_line=item.shipping_line,
        booking_no=item.booking_no,
        container_no=item.container_no,
        list_id=item.list_id,
        list_name=item.list_name,
        last_checked_at=_isoformat(item.last_checked_at),
    )


def _serialize_updated_shipment(item: UpdatedShipment) -> UpdatedShipmentResponse:
    return UpdatedShipmentResponse(
        task_id=item.task_id,
        task_name=item.task_name,
        shipping_line=item.shipping_line,
        booking_no=item.booking_no,
        container_no=item.container_no,
        status_text=item.status_text,
        location=item.location,
        event_time=_isoformat(item.event_time),
        eta_time=_isoformat(item.eta_time),
        eta_local_text=item.eta_local_text,
        latest_move_name=item.latest_move_name,
        latest_move_location=item.latest_move_location,
        latest_move_time_local_text=item.latest_move_time_local_text,
        movement_details=item.movement_details,
        vessel_voyage=item.vessel_voyage,
        list_id=item.list_id,
        list_name=item.list_name,
    )


def _serialize_sync_stats(stats: SyncStats) -> SyncResponse:
    return SyncResponse(
        total_candidates=stats.total_candidates,
        updated=len(stats.updated_items),
        unchanged=stats.unchanged,
        skipped=stats.skipped,
        candidates_by_list=stats.candidates_by_list,
        updated_by_list=stats.updated_by_list,
        unchanged_by_list=stats.unchanged_by_list,
        updated_items=[_serialize_updated_shipment(item) for item in stats.updated_items],
    )


def _serialize_ap_invoice(item: AccountsPayableInvoice) -> AccountsPayableInvoiceResponse:
    return AccountsPayableInvoiceResponse(
        task_id=item.task_id,
        task_name=item.task_name,
        task_url=item.task_url,
        invoice_number=item.invoice_number,
        vendor=item.vendor,
        amount=item.amount,
        currency=item.currency,
        status=item.status,
        due_date=_isoformat(item.due_date),
        list_id=item.list_id,
        list_name=item.list_name,
        is_closed=item.is_closed,
        is_archived=item.is_archived,
    )


def _serialize_pricing_sync_result(item: dict[str, Any]) -> PricingSyncResultResponse:
    return PricingSyncResultResponse(
        shipment_task_id=item.get("shipment_task_id"),
        shipment_custom_id=item.get("shipment_custom_id"),
        shipment_name=item.get("shipment_name"),
        quote_task_id=item.get("quote_task_id"),
        quote_custom_id=item.get("quote_custom_id"),
        quote_name=item.get("quote_name"),
        match_selector=item.get("match_selector"),
        match_value=item.get("match_value"),
        dry_run=bool(item.get("dry_run")),
        applied_updates=int(item.get("applied_updates") or 0),
        updates=[
            PricingFieldUpdateResponse(
                field_id=str(update.get("field_id") or ""),
                field_name=str(update.get("field_name") or ""),
                value=update.get("value"),
                source_value=update.get("source_value"),
                existing_value=update.get("existing_value"),
                transform=update.get("transform"),
            )
            for update in item.get("updates", [])
            if isinstance(update, dict)
        ],
        skip_reason=item.get("skip_reason"),
    )


def _serialize_one_booking_result(item: OneBookingResult) -> OneBookingResponse:
    return OneBookingResponse(
        status_text=item.status_text,
        booking_request_no=item.booking_request_no,
        booking_no=item.booking_no,
        confirmation_no=item.confirmation_no,
        reference=item.reference,
        reference_type=item.reference_type,
        source_url=item.source_url,
        raw_source=item.raw_source,
        raw_response=item.raw_response,
    )


def _serialize_one_booking_config_report(item: OneBookingConfigReport) -> OneBookingRequirementsResponse:
    return OneBookingRequirementsResponse(
        configured=item.configured,
        live_ready=item.live_ready,
        required_items=item.required_items,
        missing_required_items=item.missing_required_items,
        recommended_items=item.recommended_items,
        missing_recommended_items=item.missing_recommended_items,
        notes=item.notes,
    )


def _serialize_track_trace_config_report(item: TrackTraceConfigReport) -> TrackTraceRequirementsResponse:
    return TrackTraceRequirementsResponse(
        configured=item.configured,
        live_ready=item.live_ready,
        required_items=item.required_items,
        missing_required_items=item.missing_required_items,
        recommended_items=item.recommended_items,
        missing_recommended_items=item.missing_recommended_items,
        notes=item.notes,
    )


def _serialize_audit_run(item: AuditRun) -> AuditRunResponse:
    return AuditRunResponse(
        id=item.id,
        source=item.source,
        started_at=item.started_at,
        finished_at=item.finished_at,
        status=item.status,
        host=item.host,
        allowed_lines=item.allowed_lines,
        total_candidates=item.total_candidates,
        updated=item.updated,
        unchanged=item.unchanged,
        skipped=item.skipped,
        error=item.error,
    )


def _extract_presented_token(
    *,
    authorization: str | None,
    x_trigger_token: str | None,
    query_token: str | None,
    allow_query_token: bool = False,
) -> str | None:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    if x_trigger_token and x_trigger_token.strip():
        return x_trigger_token.strip()
    if allow_query_token and query_token and query_token.strip():
        return query_token.strip()
    return None


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
