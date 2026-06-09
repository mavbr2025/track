from __future__ import annotations

from shipment_sync.carriers.wan_hai import _build_reference_attempts, _extract_booking_detail_containers, _status_from_parsed
from shipment_sync.models import ShipmentRef


def test_wan_hai_prefers_booking_before_existing_containers() -> None:
    attempts = _build_reference_attempts(
        ShipmentRef(
            task_id="task-1",
            task_name="Wan Hai shipment",
            shipping_line="wan hai",
            booking_no="031G539204",
            container_no="WHSU4002923, WHSU4041560, BEAU5922059",
            list_id="list-1",
        )
    )

    assert attempts[:2] == [("031G539204", "2"), ("WHSU4002923", "1")]


def test_wan_hai_extracts_containers_from_booking_detail_table_only() -> None:
    html = """
    <html>
      <body>
        <script>var unrelated = "BEAU5922059";</script>
        <table><tr><th>Ctnr No.</th><th>Seal No.</th></tr>
          <tr><td>WHSU4002923</td><td>A</td></tr>
          <tr><td>WHSU4020402</td><td>B</td></tr>
          <tr><td>WHSU4041560</td><td>C</td></tr>
        </table>
      </body>
    </html>
    """

    assert _extract_booking_detail_containers(html) == [
        "WHSU4002923",
        "WHSU4020402",
        "WHSU4041560",
    ]


def test_wan_hai_status_sets_vessel_voyage_from_booking_detail() -> None:
    status = _status_from_parsed(
        cargo_type="2",
        result={"vessel": "KOTA SANTOS", "voyage": "020E", "booking_reference": "031G539204"},
        detail={
            "vessel": "KOTA SANTOS",
            "voyage": "020E",
            "booking_status": "CONFIRMED",
            "estimated_arrival_date": "2026/06/24",
            "port_of_loading": "NINGBO (CN)",
            "port_of_discharging": "PUERTO QUETZAL (GT)",
        },
        discovered_containers=["WHSU4002923"],
        source_url="https://www.wanhai.com/views/cargo_track_v2/tracking_data_list.xhtml",
    )

    assert status.vessel_voyage == "KOTA SANTOS 020E"
