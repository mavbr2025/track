from shipment_sync.carriers.common import (
    extract_event_state_hint,
    extract_event_vessel_voyage,
    extract_final_destination_vessel_voyage,
)


def test_extract_event_state_hint_prefers_trigger_type() -> None:
    event = {
        "triggerType": "ACTUAL",
        "status": "estimated",
    }

    assert extract_event_state_hint(event) == "ACTUAL"


def test_extract_event_state_hint_reads_boolean_actual_indicator() -> None:
    event = {
        "actualIndicator": True,
        "status": "planned",
    }

    assert extract_event_state_hint(event) == "actual"


def test_extract_event_state_hint_reads_boolean_estimated_indicator() -> None:
    event = {
        "estimatedIndicator": "yes",
        "status": "completed",
    }

    assert extract_event_state_hint(event) == "estimated"


def test_extract_event_state_hint_uses_extra_keys() -> None:
    event = {
        "label": "confirmed",
    }

    assert extract_event_state_hint(event, extra_keys=["label"]) == "confirmed"


def test_extract_final_destination_vessel_voyage_uses_final_arrival_not_origin_departure() -> None:
    events = [
        {
            "transportEventTypeCode": "DEPA",
            "eventDateTime": "2026-03-16T18:00:00Z",
            "locationName": "BARCELONA",
            "vesselName": "CMA CGM IMAGINATION",
            "carrierExportVoyageNumber": "0DVOMS1MA",
        },
        {
            "transportEventTypeCode": "ARRI",
            "eventDateTime": "2026-04-06T14:48:00Z",
            "locationName": "KINGSTON",
            "vesselName": "CMA CGM IMAGINATION",
            "carrierExportVoyageNumber": "0DVOMS1MA",
        },
        {
            "transportEventTypeCode": "ARRI",
            "eventDateTime": "2026-04-17T18:00:00Z",
            "locationName": "SANTO TOMAS DE CASTILLA",
            "vesselName": "A. OBELIX",
            "carrierExportVoyageNumber": "0YKCVN1MA",
        },
    ]

    assert extract_final_destination_vessel_voyage(events) == "A. OBELIX 0YKCVN1MA"


def test_extract_event_vessel_voyage_supports_nested_and_msc_style_fields() -> None:
    assert extract_event_vessel_voyage(
        {"vesselVoyage": {"vesselName": "MSC MOTHER", "voyageNo": "123E"}}
    ) == "MSC MOTHER 123E"
    assert extract_event_vessel_voyage(
        {"VesselName": "MSC HEFEI", "Voyage": "456W"}
    ) == "MSC HEFEI 456W"
