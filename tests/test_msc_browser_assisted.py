from shipment_sync.models import ShipmentRef
from shipment_sync.msc_browser_assisted import build_queue, is_msc_line, status_from_browser_capture


def test_build_queue_uses_container_references_before_booking() -> None:
    items = build_queue(
        [
            ShipmentRef(
                task_id="task-1",
                task_name="MSC shipment",
                shipping_line="MSC",
                booking_no="BOOK-1",
                container_no="TRHU5066421, CAIU7832977",
                list_id="list-1",
            )
        ]
    )

    assert len(items) == 1
    assert items[0].container_numbers == ["TRHU5066421", "CAIU7832977"]
    assert items[0].booking_no == "BOOK-1"


def test_msc_line_normalization_accepts_registered_aliases() -> None:
    assert is_msc_line("MSC")
    assert is_msc_line("Mediterranean Shipping Company")
    assert not is_msc_line("ONE")


def test_browser_capture_maps_visible_msc_result_to_existing_status_model() -> None:
    capture = """\
CONTAINER NUMBER: TRHU5066421
POD ETA
19/09/2026
Date
Location
Description
Empty/Laden/Vessel/Voyage
Equipment handling facility name
19/09/2026
Miami, US
Estimated Time of Arrival
ANTWERP 81W
Pomtoc Terminal
12/08/2026
Busan, KR
Full Intended Transshipment
ANTWERP 81E
Busan Container Terminal - Bct
08/08/2026
Busan, KR
Full Transshipment Discharged
MSC YOKOHAMA GY631A
Pusan New Port International Terminal (Pnit)
06/08/2026
Ningbo, CN
Export Loaded on Vessel
MSC YOKOHAMA GY631A
Gangji Terminal (Phase Iv)
"""

    status = status_from_browser_capture(capture)

    assert status.eta_time is not None
    assert status.eta_time.date().isoformat() == "2026-09-19"
    assert status.vessel_voyage == "ANTWERP 81W"
    assert status.discovered_containers == ["TRHU5066421"]
    assert not status.container_discovery_authoritative
    assert any(move.name == "Container Loaded (LOAD)" for move in status.recent_moves)
