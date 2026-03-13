from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests

from .contact_config import ContactSyncSettings
from .contact_models import ContactRecord

DAV_NS = "DAV:"
CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"
NS = {"d": DAV_NS, "c": CARDDAV_NS}


class iCloudCardDAVClient:
    def __init__(self, settings: ContactSyncSettings):
        self.settings = settings
        self.session = requests.Session()
        self.session.auth = (settings.icloud_apple_id, settings.icloud_app_specific_password)
        self.session.headers.update({"User-Agent": "clickup-icloud-contacts-sync/0.1"})
        self._addressbook_url: str | None = None

    def ensure_addressbook_url(self) -> str:
        if self._addressbook_url:
            return self._addressbook_url

        if self.settings.icloud_addressbook_url:
            self._addressbook_url = _normalize_collection_url(self.settings.icloud_addressbook_url)
            return self._addressbook_url

        base_url = _normalize_collection_url(self.settings.icloud_carddav_url)
        principal_url = self._discover_current_user_principal(base_url)
        home_url = self._discover_addressbook_home(principal_url)
        self._addressbook_url = self._discover_default_addressbook(home_url)
        return self._addressbook_url

    def upsert_contact(self, contact: ContactRecord) -> requests.Response:
        addressbook_url = self.ensure_addressbook_url()
        uid = _uid_for_task(contact.task_id)
        object_url = urljoin(addressbook_url, f"{uid}.vcf")
        payload = build_vcard(contact, uid=uid)
        response = self.session.put(
            object_url,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            timeout=self.settings.icloud_timeout_seconds,
        )
        response.raise_for_status()
        return response

    def _discover_current_user_principal(self, base_url: str) -> str:
        body = """
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:current-user-principal />
  </d:prop>
</d:propfind>
""".strip()
        root = self._propfind(base_url, depth="0", body=body)
        for response_el in root.findall("d:response", NS):
            href_el = response_el.find("d:propstat/d:prop/d:current-user-principal/d:href", NS)
            if href_el is not None and href_el.text:
                return _absolute_href(base_url, href_el.text)
        raise RuntimeError(
            "Unable to discover current-user-principal from CardDAV endpoint. "
            "Set ICLOUD_ADDRESSBOOK_URL explicitly."
        )

    def _discover_addressbook_home(self, principal_url: str) -> str:
        body = """
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <c:addressbook-home-set />
  </d:prop>
</d:propfind>
""".strip()
        root = self._propfind(principal_url, depth="0", body=body)
        for response_el in root.findall("d:response", NS):
            href_el = response_el.find("d:propstat/d:prop/c:addressbook-home-set/d:href", NS)
            if href_el is not None and href_el.text:
                return _absolute_href(principal_url, href_el.text)
        raise RuntimeError(
            "Unable to discover addressbook-home-set from principal URL. "
            "Set ICLOUD_ADDRESSBOOK_URL explicitly."
        )

    def _discover_default_addressbook(self, home_url: str) -> str:
        body = """
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:resourcetype />
    <d:displayname />
  </d:prop>
</d:propfind>
""".strip()
        root = self._propfind(home_url, depth="1", body=body)

        for response_el in root.findall("d:response", NS):
            href_el = response_el.find("d:href", NS)
            if href_el is None or not href_el.text:
                continue
            candidate_url = _absolute_href(home_url, href_el.text)
            if _is_addressbook_resource(response_el):
                return _normalize_collection_url(candidate_url)

        raise RuntimeError(
            "Unable to locate a CardDAV addressbook collection. "
            "Set ICLOUD_ADDRESSBOOK_URL explicitly."
        )

    def _propfind(self, url: str, *, depth: str, body: str) -> ET.Element:
        response = self.session.request(
            "PROPFIND",
            url,
            data=body.encode("utf-8"),
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
            timeout=self.settings.icloud_timeout_seconds,
            allow_redirects=False,
        )
        if response.status_code not in {200, 207}:
            raise RuntimeError(f"CardDAV PROPFIND failed ({response.status_code}) for {url}: {response.text[:200]}")
        return ET.fromstring(response.text)


def build_vcard(contact: ContactRecord, *, uid: str) -> str:
    first_name = _clean(contact.first_name)
    last_name = _clean(contact.last_name)
    full_name = _clean(contact.full_name) or _clean(contact.task_name) or f"ClickUp Contact {contact.task_id}"
    if not first_name and not last_name:
        first_name = full_name

    lines: list[str] = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{_vcard_escape(uid)}",
        f"FN:{_vcard_escape(full_name)}",
        f"N:{_vcard_escape(last_name or '')};{_vcard_escape(first_name or '')};;;",
        f"REV:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    ]

    if contact.email:
        lines.append(f"EMAIL;TYPE=INTERNET,WORK:{_vcard_escape(contact.email)}")
    if contact.phone:
        lines.append(f"TEL;TYPE=CELL:{_vcard_escape(contact.phone)}")
    if contact.company:
        lines.append(f"ORG:{_vcard_escape(contact.company)}")
    if contact.title:
        lines.append(f"TITLE:{_vcard_escape(contact.title)}")
    if contact.linkedin_url:
        lines.append(f"URL;TYPE=LinkedIn:{_vcard_escape(contact.linkedin_url)}")
    if contact.notes:
        lines.append(f"NOTE:{_vcard_escape(contact.notes)}")

    lines.append("END:VCARD")
    return "\r\n".join(_fold_lines(lines)) + "\r\n"


def _fold_lines(lines: Iterable[str]) -> list[str]:
    folded: list[str] = []
    for line in lines:
        if len(line) <= 75:
            folded.append(line)
            continue
        start = 0
        while start < len(line):
            end = min(start + 75, len(line))
            chunk = line[start:end]
            if start == 0:
                folded.append(chunk)
            else:
                folded.append(f" {chunk}")
            start = end
    return folded


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _vcard_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _absolute_href(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def _normalize_collection_url(url: str) -> str:
    stripped = url.strip()
    if not stripped.endswith("/"):
        stripped += "/"
    return stripped


def _uid_for_task(task_id: str) -> str:
    return f"clickup-task-{task_id}"


def _is_addressbook_resource(response_el: ET.Element) -> bool:
    resourcetype = response_el.find("d:propstat/d:prop/d:resourcetype", NS)
    if resourcetype is None:
        return False
    has_addressbook = False
    for child in list(resourcetype):
        tag = child.tag
        if tag == f"{{{CARDDAV_NS}}}addressbook":
            has_addressbook = True
    return has_addressbook
