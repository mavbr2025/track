from __future__ import annotations

from shipment_sync.carriers.msc import _build_reference_attempts, _limit_reference_attempts, _status_from_payload
from shipment_sync.models import ShipmentRef


def test_msc_tries_booking_before_container_references() -> None:
    attempts = _build_reference_attempts(
        ShipmentRef(
            task_id="task-1",
            task_name="MSC shipment",
            shipping_line="msc",
            booking_no="MEDUR0151668",
            container_no="BMOU5636281, CAIU7832977 MSCU5227020",
            list_id="list-1",
        )
    )

    assert attempts == [
        ("MEDUR0151668", "1"),
        ("BMOU5636281", "0"),
        ("CAIU7832977", "0"),
        ("MSCU5227020", "0"),
    ]


def test_msc_reference_attempts_are_deduped() -> None:
    attempts = _build_reference_attempts(
        ShipmentRef(
            task_id="task-1",
            task_name="MSC shipment",
            shipping_line="msc",
            booking_no=None,
            container_no="bmou5636281, BMOU5636281",
            list_id="list-1",
        )
    )

    assert attempts == [("BMOU5636281", "0")]


def test_msc_reference_attempt_limit_keeps_booking_fallback() -> None:
    attempts = _build_reference_attempts(
        ShipmentRef(
            task_id="task-1",
            task_name="MSC shipment",
            shipping_line="msc",
            booking_no="MEDUR0151668",
            container_no="BMOU5636281, CAIU7832977 MSCU5227020 TCLU1234567",
            list_id="list-1",
        )
    )

    assert _limit_reference_attempts(attempts, limit=3) == [
        ("MEDUR0151668", "1"),
        ("BMOU5636281", "0"),
        ("CAIU7832977", "0"),
    ]


def test_msc_booking_reference_strips_non_breaking_spaces() -> None:
    attempts = _build_reference_attempts(
        ShipmentRef(
            task_id="task-1",
            task_name="MSC shipment",
            shipping_line="msc",
            booking_no="177WJUJUJ308516W\u00a0",
            container_no="GAOU7846820, GAOU7964083",
            list_id="list-1",
        )
    )

    assert attempts[0] == ("177WJUJUJ308516W", "1")


def test_msc_status_sets_vessel_voyage_from_final_pod_fields() -> None:
    status = _status_from_payload(
        payload={
            "IsSuccess": True,
            "Data": {
                "BillOfLadings": [
                    {
                        "FinalPodVesselName": "MSC FINAL",
                        "FinalPodVoyage": "FV123A",
                        "ContainersInfo": [
                            {
                                "PodEtaDate": "2026-06-04",
                                "Events": [
                                    {
                                        "Description": "Loaded",
                                        "Date": "2026-05-01",
                                        "Location": "NINGBO",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        },
        source="msc-playwright:test",
        source_url="https://www.msc.com/en/track-a-shipment",
        eta_only_mode=True,
    )

    assert status.vessel_voyage == "MSC FINAL FV123A"


def test_msc_normalizes_terminal_delivery_and_empty_return_events() -> None:
    status = _status_from_payload(
        payload={
            "IsSuccess": True,
            "Data": {
                "BillOfLadings": [
                    {
                        "ContainersInfo": [
                            {
                                "Events": [
                                    {
                                        "Description": "Import to consignee",
                                        "Date": "14/07/2026",
                                        "Location": "MIAMI, US",
                                    },
                                    {
                                        "Description": "Empty received at CY",
                                        "Date": "20/07/2026",
                                        "Location": "MIAMI, US",
                                    },
                                ],
                            }
                        ],
                    }
                ]
            },
        },
        source="msc-playwright:test",
        source_url="https://www.msc.com/en/track-a-shipment",
        eta_only_mode=True,
    )

    assert [move.name for move in status.recent_moves] == [
        "Container Gated In (GTIN)",
        "Container Gated Out (GTOT)",
    ]
