from shipment_sync.carriers.common import extract_event_state_hint


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
