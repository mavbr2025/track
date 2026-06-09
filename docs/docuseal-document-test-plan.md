# DocuSeal Document Integration Test Plan

This test plan uses `/Users/mario/Downloads/Nondisclosure Agreement MTM | ​Supply Chain Worldwide S de RL de CV.pdf` as the NDA reference document.

## PDF Findings

- The file is a completed signed NDA, not a blank template.
- The PDF has 4 pages: 3 agreement pages plus 1 audit-trail page.
- The PDF contains an AcroForm and a valid digital signature from Contractbook.
- Use this file to define field names and layout, then create a clean DocuSeal template for live submissions.

## Test Endpoints

```text
GET  /api/documents/requirements
POST /api/documents/nda
POST /api/webhooks/clickup/credit-contract
POST /api/webhooks/docuseal
```

The issuing endpoints default to `dry_run: true`, so the first tests return the exact DocuSeal payload without sending emails.

## NDA Flow

The standalone NDA does not depend on ClickUp.

```text
MTM Logix API -> DocuSeal NDA template -> signer emails/signing links -> DocuSeal webhook -> MTM Logix API
```

Test payload:

```json
{
  "dry_run": true,
  "external_reference": "nda-test-001",
  "effective_date": "2026-05-12",
  "counterparty": {
    "company_name": "Supply Chain Worldwide S de RL de CV",
    "company_address": "Av. Lerma 1C BIS 1, INT1A, Col. San Pedro Tultepec, Lerma, Estado de Mexico, C.P. 52030, Mexico.",
    "company_tax_id": "SLM180209EN7"
  },
  "counterparty_signer": {
    "name": "Jose Valdes",
    "title": "Receiving Party",
    "email": "customer@example.com"
  },
  "mtm_signer": {
    "name": "Mario Veraldo",
    "title": "Chief Executive Officer",
    "email": "mario@mtmlogix.com"
  }
}
```

Template roles expected in DocuSeal:

```text
Disclosing Party
Receiving Party
```

Suggested DocuSeal field names:

```text
Effective Date
Disclosing Party Company Name
Disclosing Party Company Address
Disclosing Party Tax ID
Receiving Party Company Name
Receiving Party Company Address
Receiving Party Tax ID
Disclosing Party Signer Name
Disclosing Party Signer Title
Disclosing Party Signer Email
Receiving Party Signer Name
Receiving Party Signer Title
Receiving Party Signer Email
```

## Credit Contract Flow

Credit contracts are ClickUp-triggered.

```text
ClickUp Automation -> /api/webhooks/clickup/credit-contract -> DocuSeal credit contract template
DocuSeal completion webhook -> /api/webhooks/docuseal -> update/store against ClickUp task
```

Minimum normalized fields needed from ClickUp:

```text
task_id
task_url
customer_company_name
signer_name
signer_email
```

The DocuSeal `external_id` should include the ClickUp task id so completion webhooks can be linked back to the originating task.

## Environment

```text
DOCUSEAL_API_URL=https://api.docuseal.com
DOCUSEAL_API_KEY=
DOCUSEAL_NDA_TEMPLATE_ID=
DOCUSEAL_CREDIT_CONTRACT_TEMPLATE_ID=
DOCUSEAL_WEBHOOK_TOKEN=
SHIPMENT_API_TRIGGER_TOKEN=
DOCUSEAL_SEND_EMAIL_DEFAULT=false
```

DocuSeal document URLs expire, so the system should store DocuSeal submission/submitter ids, not temporary document URLs. Fetch fresh document URLs through DocuSeal when needed.
