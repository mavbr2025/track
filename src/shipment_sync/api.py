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
from shipment_sync.clickup_ap_client import ClickUpAccountsPayableClient
from shipment_sync.clickup_client import ClickUpClient
from shipment_sync.config import Settings
from shipment_sync.models import ShipmentRef
from shipment_sync.one_booking_client import OneBookingClient
from shipment_sync.one_booking_config import OneBookingConfigReport, OneBookingSettings
from shipment_sync.one_booking_models import OneBookingResult
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


def create_app() -> FastAPI:
    app = FastAPI(
        title="Shipment Sync API",
        version="0.1.0",
        description="HTTP API for previewing ClickUp shipment tasks and running shipment sync jobs.",
    )

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


def _get_one_booking_client() -> OneBookingClient:
    load_dotenv()
    try:
        settings = OneBookingSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OneBookingClient(settings)


def _require_operator_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_trigger_token: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> None:
    load_dotenv()
    settings = ApiTriggerSettings.from_env()
    expected = settings.trigger_token
    if not expected:
        return

    provided = _extract_presented_token(authorization=authorization, x_trigger_token=x_trigger_token, query_token=token)
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


def _extract_presented_token(
    *,
    authorization: str | None,
    x_trigger_token: str | None,
    query_token: str | None,
) -> str | None:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    if x_trigger_token and x_trigger_token.strip():
        return x_trigger_token.strip()
    if query_token and query_token.strip():
        return query_token.strip()
    return None


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
