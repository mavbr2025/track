from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class DocumentIntegrationConfigReport:
    configured: bool
    live_ready: bool
    required_items: list[str]
    missing_required_items: list[str]
    recommended_items: list[str]
    missing_recommended_items: list[str]
    notes: list[str]


@dataclass(frozen=True)
class DocumentIntegrationSettings:
    docuseal_api_url: str
    docuseal_api_key: str | None
    docuseal_nda_template_id: int | None
    docuseal_credit_contract_template_id: int | None
    docuseal_webhook_token: str | None
    docuseal_send_email_default: bool
    nda_mtm_role: str
    nda_counterparty_role: str
    credit_contract_signer_role: str
    mtm_company_name: str
    mtm_company_address: str
    mtm_company_tax_id: str
    mtm_default_signer_name: str | None
    mtm_default_signer_title: str | None
    mtm_default_signer_email: str | None

    @classmethod
    def from_env(cls) -> "DocumentIntegrationSettings":
        return cls(
            docuseal_api_url=_optional("DOCUSEAL_API_URL") or "https://api.docuseal.com",
            docuseal_api_key=_optional("DOCUSEAL_API_KEY"),
            docuseal_nda_template_id=_int_optional("DOCUSEAL_NDA_TEMPLATE_ID"),
            docuseal_credit_contract_template_id=_int_optional("DOCUSEAL_CREDIT_CONTRACT_TEMPLATE_ID"),
            docuseal_webhook_token=_optional("DOCUSEAL_WEBHOOK_TOKEN"),
            docuseal_send_email_default=_bool("DOCUSEAL_SEND_EMAIL_DEFAULT", default=False),
            nda_mtm_role=_optional("DOCUSEAL_NDA_MTM_ROLE") or "Disclosing Party",
            nda_counterparty_role=_optional("DOCUSEAL_NDA_COUNTERPARTY_ROLE") or "Receiving Party",
            credit_contract_signer_role=_optional("DOCUSEAL_CREDIT_CONTRACT_SIGNER_ROLE") or "Customer",
            mtm_company_name=_optional("MTM_LEGAL_NAME") or "MTM Logix, Inc",
            mtm_company_address=_optional("MTM_LEGAL_ADDRESS") or "5 Penn Plaza 19th Floor New York NY 10001 USA",
            mtm_company_tax_id=_optional("MTM_TAX_ID") or "EIN: 92-0754958",
            mtm_default_signer_name=_optional("MTM_NDA_SIGNER_NAME"),
            mtm_default_signer_title=_optional("MTM_NDA_SIGNER_TITLE"),
            mtm_default_signer_email=_optional("MTM_NDA_SIGNER_EMAIL"),
        )


def inspect_document_integration_env() -> DocumentIntegrationConfigReport:
    required_items = [
        "DOCUSEAL_API_KEY",
        "DOCUSEAL_NDA_TEMPLATE_ID",
    ]
    recommended_items = [
        "DOCUSEAL_WEBHOOK_TOKEN",
        "SHIPMENT_API_TRIGGER_TOKEN",
        "MTM_NDA_SIGNER_NAME",
        "MTM_NDA_SIGNER_EMAIL",
        "DOCUSEAL_CREDIT_CONTRACT_TEMPLATE_ID",
        "CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN",
    ]

    missing_required_items = [key for key in required_items if not _optional(key)]
    missing_recommended_items: list[str] = []
    for key in ("DOCUSEAL_WEBHOOK_TOKEN", "SHIPMENT_API_TRIGGER_TOKEN", "MTM_NDA_SIGNER_NAME", "MTM_NDA_SIGNER_EMAIL"):
        if not _optional(key):
            missing_recommended_items.append(key)
    if not _optional("DOCUSEAL_CREDIT_CONTRACT_TEMPLATE_ID"):
        missing_recommended_items.append("DOCUSEAL_CREDIT_CONTRACT_TEMPLATE_ID")
    if not _optional("CLICKUP_OAUTH_ACCESS_TOKEN") and not _optional("CLICKUP_API_TOKEN"):
        missing_recommended_items.append("CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN")

    notes = [
        "The provided NDA PDF is a signed reference; create a clean DocuSeal NDA template before live sends.",
        "NDA requests are standalone and do not require ClickUp.",
        "Credit contract requests should carry the ClickUp task id in DocuSeal metadata/external_id.",
        "DocuSeal document URLs expire; store submission_id/submitter_id and fetch fresh URLs when needed.",
    ]

    return DocumentIntegrationConfigReport(
        configured=not missing_required_items,
        live_ready=not missing_required_items and not any(
            item in missing_recommended_items for item in ("DOCUSEAL_WEBHOOK_TOKEN", "SHIPMENT_API_TRIGGER_TOKEN")
        ),
        required_items=required_items,
        missing_required_items=missing_required_items,
        recommended_items=recommended_items,
        missing_recommended_items=missing_recommended_items,
        notes=notes,
    )


def _optional(key: str) -> str | None:
    value = os.getenv(key)
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def _int_optional(key: str) -> int | None:
    value = _optional(key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bool(key: str, *, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
