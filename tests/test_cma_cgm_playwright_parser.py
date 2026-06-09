from datetime import timezone

from shipment_sync.carriers.cma_cgm import _status_from_playwright_text


CMA_TRACKING_DETAIL_TEXT = """
Tracking details
EMPTY IN DEPOT
Container
TCKU3341228
ARRIVED AT POD
Sat 18-APR-2026
04:22 PM
Wednesday, 11-MAR-2026
09:36 AM
EMPTY TO SHIPPER
BARCELONA
Monday, 16-MAR-2026
10:36 AM
LOADED ON BOARD
BARCELONA
CMA CGM IMAGINATION (0DVOMS1MA)
Monday, 16-MAR-2026
06:00 PM
VESSEL DEPARTURE
BARCELONA
CMA CGM IMAGINATION (0DVOMS1MA)
Monday, 06-APR-2026
02:48 PM
VESSEL ARRIVAL
KINGSTON
CMA CGM IMAGINATION (0DVOMS1MA)
Tuesday, 07-APR-2026
01:22 AM
DISCHARGED IN TRANSHIPMENT
KINGSTON
CMA CGM IMAGINATION (0DVOMS1MA)
Monday, 13-APR-2026
01:54 PM
LOADED ON BOARD
KINGSTON
A. OBELIX (0YKCVN1MA)
Monday, 13-APR-2026
06:06 PM
VESSEL DEPARTURE
KINGSTON
A. OBELIX (0YKCVN1MA)
Friday, 17-APR-2026
06:00 PM
VESSEL ARRIVAL
SANTO TOMAS DE CASTILLA
A. OBELIX (0YKCVN1MA)
Saturday, 18-APR-2026
04:22 PM
DISCHARGED
SANTO TOMAS DE CASTILLA
A. OBELIX (0YKCVN1MA)
Sunday, 26-APR-2026
02:35 AM
CONTAINER TO CONSIGNEE
SANTO TOMAS DE CASTILLA
Monday, 27-APR-2026
06:02 PM
EMPTY IN DEPOT
SANTO TOMAS DE CASTILLA
"""


def test_cma_playwright_text_maps_tracking_detail_to_status() -> None:
    status = _status_from_playwright_text(
        CMA_TRACKING_DETAIL_TEXT,
        reference="TCKU3341228",
        source_url="https://www.cma-cgm.com/ebusiness/tracking/detail/TCKU3341228",
        raw_source="cma-playwright:test",
    )

    assert status.status_text == "EMPTY IN DEPOT"
    assert status.eta_local_text == "2026-04-18T16:22:00"
    assert status.eta_time is not None
    assert status.eta_time.tzinfo == timezone.utc
    assert status.discovered_containers == ["TCKU3341228"]

    assert len(status.recent_moves) == 11
    assert status.latest_move is not None
    assert status.latest_move.name == "Container Gated In (GTIN)"
    assert status.latest_move.location == "SANTO TOMAS DE CASTILLA"
    assert status.latest_move.event_time_local_text == "2026-04-27T18:02:00"
    assert status.vessel_voyage == "A. OBELIX (0YKCVN1MA)"

    move_pairs = {(move.name, move.event_time_local_text, move.location) for move in status.recent_moves}
    assert ("Transport Departed (DEPA)", "2026-03-16T18:00:00", "BARCELONA") in move_pairs
    assert ("Container Discharged (DISC)", "2026-04-18T16:22:00", "SANTO TOMAS DE CASTILLA") in move_pairs
    assert ("Container Gated Out (GTOT)", "2026-04-26T02:35:00", "SANTO TOMAS DE CASTILLA") in move_pairs
