"""Template adapter using a carrier HTTP endpoint.

Copy this file and implement `fetch_status` for each shipping line.
"""

import requests

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.common import parse_event_time
from shipment_sync.models import ShipmentRef, ShipmentStatus


class RequestsTemplateAdapter(CarrierAdapter):
    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        ref = shipment.container_no or shipment.booking_no
        if not ref:
            raise ValueError("Missing shipment reference")

        # Example only: replace URL, params, and parsing logic with your carrier API.
        response = requests.get("https://example.com/track", params={"reference": ref}, timeout=30)
        response.raise_for_status()
        payload = response.json()

        return ShipmentStatus(
            status_text=str(payload.get("status", "Unknown")),
            location=payload.get("location"),
            eta_time=parse_event_time(payload.get("eta")),
            raw_source="carrier-api",
        )
