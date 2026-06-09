from __future__ import annotations

from typing import Any

import requests

from shipment_sync.document_config import DocumentIntegrationSettings


class DocuSealClient:
    def __init__(self, settings: DocumentIntegrationSettings):
        if not settings.docuseal_api_key:
            raise ValueError("Missing DOCUSEAL_API_KEY")
        self.base_url = settings.docuseal_api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "X-Auth-Token": settings.docuseal_api_key,
            }
        )

    def create_submission(self, payload: dict[str, Any]) -> Any:
        response = self.session.post(f"{self.base_url}/submissions", json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_submission_documents(self, submission_id: int | str, *, merge: bool = False) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/submissions/{submission_id}/documents",
            params={"merge": str(merge).lower()},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
