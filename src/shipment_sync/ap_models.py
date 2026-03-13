from dataclasses import dataclass
from datetime import datetime


@dataclass
class AccountsPayableInvoice:
    task_id: str
    task_name: str
    task_url: str | None
    invoice_number: str | None
    vendor: str | None
    amount: str | None
    currency: str | None
    status: str | None
    due_date: datetime | None
    list_id: str
    list_name: str | None = None
    is_closed: bool = False
    is_archived: bool = False
