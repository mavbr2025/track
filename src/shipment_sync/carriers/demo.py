from datetime import datetime, timedelta, timezone

from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.models import ShipmentRef, ShipmentStatus


class DemoCarrierAdapter(CarrierAdapter):
    """Replace this with real API or browser automation for each shipping line."""

    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        ref = shipment.container_no or shipment.booking_no or "unknown"
        return ShipmentStatus(
            status_text=f"In transit (demo) for {ref}",
            location="N/A",
            event_time=datetime.now(timezone.utc),
            eta_time=datetime.now(timezone.utc) + timedelta(days=7),
            raw_source="demo-adapter",
        )
