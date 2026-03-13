from abc import ABC, abstractmethod

from shipment_sync.models import ShipmentRef, ShipmentStatus


class CarrierAdapter(ABC):
    @abstractmethod
    def fetch_status(self, shipment: ShipmentRef) -> ShipmentStatus:
        raise NotImplementedError
