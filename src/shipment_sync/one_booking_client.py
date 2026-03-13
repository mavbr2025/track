from __future__ import annotations

import time
from typing import Any

import requests

from shipment_sync.carriers.common import extract_first, extract_json_from_http_response
from shipment_sync.one_booking_config import OneBookingSettings
from shipment_sync.one_booking_models import OneBookingResult


class OneBookingClient:
    def __init__(self, settings: OneBookingSettings) -> None:
        self.settings = settings
        self.session = requests.Session()

    def submit_booking_request(self, payload: dict[str, Any]) -> OneBookingResult:
        response = self._request(
            method="POST",
            endpoint=self.settings.request_endpoint,
            json=payload,
        )
        parsed = self._parse_json_response(response)
        return _serialize_booking_result(
            parsed,
            reference=_pick_first(parsed, ["bookingRequestNo", "bookingRequestNumber", "bookingRequestId"]),
            reference_type="booking_request",
            source_url=self.settings.request_page_url,
            raw_source=f"one-booking-request:{response.request.method}:{response.url}",
        )

    def fetch_booking_confirmation(self, reference: str, reference_type: str) -> OneBookingResult:
        endpoint = self.settings.confirmation_endpoint.format(
            reference=reference,
            reference_type=reference_type,
        )
        params = _build_confirmation_params(
            endpoint=endpoint,
            reference=reference,
            reference_type=reference_type,
            ref_param=self.settings.confirmation_ref_param,
            type_param=self.settings.confirmation_type_param,
        )
        response = self._request(
            method="GET",
            endpoint=endpoint,
            params=params,
        )
        parsed = self._parse_json_response(response)
        return _serialize_booking_result(
            parsed,
            reference=reference,
            reference_type=reference_type,
            source_url=self.settings.confirmation_page_url,
            raw_source=f"one-booking-confirmation:{response.request.method}:{response.url}",
        )

    def _request(
        self,
        *,
        method: str,
        endpoint: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = _build_url(self.settings.api_base_url, endpoint)
        headers = self._headers()

        last_error: requests.RequestException | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=self.settings.timeout_seconds,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    break
                time.sleep(self.settings.retry_delay_seconds * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError("ONE booking request failed without a specific error")

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.settings.api_key:
            headers[self.settings.api_key_header] = self.settings.api_key
        if self.settings.bearer_token:
            headers["Authorization"] = f"Bearer {self.settings.bearer_token}"
        return headers

    @staticmethod
    def _parse_json_response(response: requests.Response) -> dict[str, Any]:
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" in content_type:
            data = response.json()
            if isinstance(data, dict):
                return data
            return {"data": data}
        return extract_json_from_http_response(response)


def _serialize_booking_result(
    payload: dict[str, Any],
    *,
    reference: str | None,
    reference_type: str | None,
    source_url: str | None,
    raw_source: str | None,
) -> OneBookingResult:
    status_text = _pick_first(
        payload,
        [
            "bookingStatus",
            "bookingStatusName",
            "bookingStatusDescription",
            "bookingRequestStatus",
            "requestStatus",
            "confirmationStatus",
            "status",
            "statusName",
            "statusDescription",
            "resultMessage",
            "message",
        ],
    ) or "Unknown"

    booking_request_no = _pick_first(
        payload,
        [
            "bookingRequestNo",
            "bookingRequestNumber",
            "bookingRequestId",
            "bookingRequestReference",
            "requestNo",
            "requestNumber",
        ],
    )
    booking_no = _pick_first(
        payload,
        [
            "bookingNo",
            "bookingNumber",
            "bookingReference",
            "carrierBookingReference",
            "bkgNo",
        ],
    )
    confirmation_no = _pick_first(
        payload,
        [
            "confirmationNo",
            "confirmationNumber",
            "bookingConfirmationNo",
            "bookingConfirmationNumber",
            "confirmationReference",
        ],
    )

    return OneBookingResult(
        status_text=status_text,
        booking_request_no=booking_request_no,
        booking_no=booking_no,
        confirmation_no=confirmation_no,
        reference=reference or booking_request_no or booking_no or confirmation_no,
        reference_type=reference_type,
        source_url=source_url,
        raw_source=raw_source,
        raw_response=payload,
    )


def _pick_first(payload: Any, keys: list[str]) -> str | None:
    return extract_first(payload, keys)


def _build_confirmation_params(
    *,
    endpoint: str,
    reference: str,
    reference_type: str,
    ref_param: str,
    type_param: str | None,
) -> dict[str, str] | None:
    if "{reference}" in endpoint or "{reference_type}" in endpoint:
        return None

    params = {ref_param: reference}
    if type_param:
        params[type_param] = reference_type
    return params


def _build_url(base_url: str, endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
