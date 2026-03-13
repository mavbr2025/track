from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
import requests

from .ap_config import AccountsPayableSettings
from .clickup_ap_client import ClickUpAccountsPayableClient


def main() -> None:
    parser = argparse.ArgumentParser(description="List accounts payable invoices from ClickUp")
    parser.add_argument("--query", help="Optional text filter across task, invoice number, vendor, amount, status.")
    parser.add_argument("--limit", type=int, default=100, help="Max invoices to print (default: 100, 0 = all).")
    parser.add_argument(
        "--list-fields",
        action="store_true",
        help="List custom fields in the configured AP lists and exit.",
    )
    args = parser.parse_args()

    load_dotenv()
    try:
        settings = AccountsPayableSettings.from_env()
        client = ClickUpAccountsPayableClient(settings)

        if args.list_fields:
            fields = client.list_custom_fields()
            if not fields:
                print("No custom fields found for configured AP lists.")
                return
            for field in fields:
                print(
                    f"- list={field['list_id']} | id={field['id']} | "
                    f"type={field['type'] or 'unknown'} | name={field['name'] or 'unnamed'}"
                )
            return

        invoices = client.list_invoices(query=args.query)
        print(f"Invoices found: {len(invoices)}")
        print(f"Has invoices: {'yes' if invoices else 'no'}")

        visible = invoices if args.limit == 0 else invoices[: max(args.limit, 0)]
        if not visible:
            return

        for invoice in visible:
            due_text = invoice.due_date.isoformat() if invoice.due_date else "n/a"
            list_label = f"{invoice.list_name} ({invoice.list_id})" if invoice.list_name else invoice.list_id
            amount_parts = [part for part in [invoice.currency, invoice.amount] if part]
            amount_text = " ".join(amount_parts) if amount_parts else invoice.amount or "n/a"
            print(
                f"- [{list_label}] {invoice.task_name or invoice.task_id} | "
                f"invoice={invoice.invoice_number or 'n/a'} | "
                f"vendor={invoice.vendor or 'n/a'} | "
                f"status={invoice.status or 'n/a'} | "
                f"due={due_text} | "
                f"amount={amount_text}"
            )
            if invoice.task_url:
                print(f"  url={invoice.task_url}")
    except requests.HTTPError as exc:
        _print_http_error(exc)
        raise SystemExit(1) from exc
    except requests.RequestException as exc:
        print(f"Network request failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def _print_http_error(exc: requests.HTTPError) -> None:
    response = exc.response
    if response is None:
        print(f"HTTP request failed: {exc}", file=sys.stderr)
        return

    url = response.url or "unknown URL"
    status_code = response.status_code
    print(f"HTTP {status_code} while calling {url}", file=sys.stderr)

    snippet = (response.text or "").strip().replace("\n", " ")[:240]
    if snippet:
        print(f"Response snippet: {snippet}", file=sys.stderr)


if __name__ == "__main__":
    main()
