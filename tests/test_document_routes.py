from __future__ import annotations

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient

from shipment_sync.api import create_app
from shipment_sync.document_config import DocumentIntegrationSettings
from shipment_sync.document_routes import _get_document_settings


def _settings(**overrides: object) -> DocumentIntegrationSettings:
    values = {
        "docuseal_api_url": "https://api.docuseal.test",
        "docuseal_api_key": "docuseal-key",
        "docuseal_nda_template_id": 1001,
        "docuseal_credit_contract_template_id": 2002,
        "docuseal_webhook_token": None,
        "docuseal_send_email_default": False,
        "nda_mtm_role": "Disclosing Party",
        "nda_counterparty_role": "Receiving Party",
        "credit_contract_signer_role": "Customer",
        "mtm_company_name": "MTM Logix, Inc",
        "mtm_company_address": "5 Penn Plaza 19th Floor New York NY 10001 USA",
        "mtm_company_tax_id": "EIN: 92-0754958",
        "mtm_default_signer_name": None,
        "mtm_default_signer_title": None,
        "mtm_default_signer_email": None,
    }
    values.update(overrides)
    return DocumentIntegrationSettings(**values)


def _client(settings: DocumentIntegrationSettings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SHIPMENT_API_TRIGGER_TOKEN", "")
    app = create_app()
    app.dependency_overrides[_get_document_settings] = lambda: settings
    return TestClient(app)


def test_nda_dry_run_builds_docuseal_submission_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_settings(), monkeypatch)

    response = client.post(
        "/api/documents/nda",
        json={
            "dry_run": True,
            "external_reference": "nda-test-001",
            "effective_date": "2026-05-12",
            "counterparty": {
                "company_name": "Supply Chain Worldwide S de RL de CV",
                "company_address": "Av. Lerma 1C BIS 1",
                "company_tax_id": "SLM180209EN7",
            },
            "counterparty_signer": {
                "name": "Jose Valdes",
                "title": "Receiving Party",
                "email": "customer@example.com",
            },
            "mtm_signer": {
                "name": "Mario Veraldo",
                "title": "Chief Executive Officer",
                "email": "mario@mtmlogix.com",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "nda"
    assert payload["dry_run"] is True
    assert payload["external_id"] == "nda:nda-test-001"
    docuseal_payload = payload["docuseal_payload"]
    assert docuseal_payload["template_id"] == 1001
    assert docuseal_payload["send_email"] is False
    assert docuseal_payload["variables"]["document_type"] == "nda"
    assert docuseal_payload["variables"]["receiving_party_tax_id"] == "SLM180209EN7"
    assert [item["role"] for item in docuseal_payload["submitters"]] == [
        "Disclosing Party",
        "Receiving Party",
    ]
    assert docuseal_payload["submitters"][1]["external_id"] == "nda:nda-test-001:counterparty"
    assert docuseal_payload["submitters"][1]["values"]["Receiving Party Signer Name"] == "Jose Valdes"


def test_nda_requires_mtm_signer_when_env_defaults_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_settings(), monkeypatch)

    response = client.post(
        "/api/documents/nda",
        json={
            "dry_run": True,
            "counterparty": {"company_name": "ABC Logistics"},
            "counterparty_signer": {"name": "Jane Smith", "email": "jane@example.com"},
        },
    )

    assert response.status_code == 400
    assert "mtm_signer.name or MTM_NDA_SIGNER_NAME" in response.json()["detail"]


def test_credit_contract_webhook_reports_missing_mapping_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_settings(docuseal_credit_contract_template_id=None), monkeypatch)

    response = client.post(
        "/api/webhooks/clickup/credit-contract",
        json={"dry_run": True, "payload": {"task_id": "task-123"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "credit_contract"
    assert payload["missing_required_fields"] == [
        "DOCUSEAL_CREDIT_CONTRACT_TEMPLATE_ID",
        "customer_company_name",
        "signer_name",
        "signer_email",
    ]


def test_docuseal_webhook_parses_completion_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_settings(docuseal_webhook_token="secret"), monkeypatch)

    response = client.post(
        "/api/webhooks/docuseal?token=secret",
        json={
            "event_type": "form.completed",
            "data": {
                "id": 10,
                "external_id": "credit-contract:task-123",
                "metadata": {
                    "document_type": "credit_contract",
                    "clickup_task_id": "task-123",
                },
                "submission": {
                    "id": 20,
                    "status": "completed",
                    "variables": {"document_type": "credit_contract"},
                },
                "documents": [{"name": "credit-contract", "url": "https://docuseal.test/document.pdf"}],
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "event_type": "form.completed",
        "document_type": "credit_contract",
        "submission_id": 20,
        "submitter_id": 10,
        "external_id": "credit-contract:task-123",
        "clickup_task_id": "task-123",
        "documents_count": 1,
        "action": "record_completion_and_update_clickup",
    }


def test_docuseal_webhook_rejects_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_settings(docuseal_webhook_token="secret"), monkeypatch)

    response = client.post("/api/webhooks/docuseal?token=wrong", json={"event_type": "form.completed"})

    assert response.status_code == 403
