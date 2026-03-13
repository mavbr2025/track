from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContactComment:
    author: str | None
    created_at_utc: str | None
    text: str


@dataclass
class ContactRecord:
    task_id: str
    task_name: str
    task_url: str | None
    first_name: str | None
    last_name: str | None
    full_name: str
    email: str | None
    phone: str | None
    company: str | None
    title: str | None
    linkedin_url: str | None
    notes: str | None
    comments: list[ContactComment]
