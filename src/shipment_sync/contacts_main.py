from __future__ import annotations

import argparse

from dotenv import load_dotenv

from shipment_sync.clickup_contacts_client import ClickUpContactsClient
from shipment_sync.contact_config import ContactSyncSettings
from shipment_sync.contacts_sync import run_contacts_sync
from shipment_sync.icloud_carddav import iCloudCardDAVClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync ClickUp list contacts into iCloud Contacts")
    parser.add_argument("--dry-run", action="store_true", help="Print contacts without writing to iCloud")
    parser.add_argument(
        "--discover-addressbook",
        action="store_true",
        help="Only discover and print the iCloud CardDAV addressbook URL",
    )
    parser.add_argument(
        "--inspect-fields",
        action="store_true",
        help="Print ClickUp custom fields from contact list(s) to map env vars",
    )
    args = parser.parse_args()

    load_dotenv()
    settings = ContactSyncSettings.from_env(require_icloud=not (args.inspect_fields or args.dry_run))
    clickup_client = ClickUpContactsClient(settings)

    if args.inspect_fields:
        fields = clickup_client.list_custom_fields()
        if not fields:
            print("No custom fields found for configured contact list(s).")
            return
        print(f"Found {len(fields)} custom fields:")
        for f in fields:
            print(f"- list={f['list_id']} | id={f['id']} | name={f['name']} | type={f['type']}")
        return

    if args.discover_addressbook:
        icloud_client = iCloudCardDAVClient(settings)
        print(icloud_client.ensure_addressbook_url())
        return

    icloud_client = iCloudCardDAVClient(settings) if not args.dry_run else None
    stats = run_contacts_sync(clickup_client, icloud_client, dry_run=args.dry_run)
    print(
        "Contact sync complete. "
        f"Candidates: {stats.total_candidates}, Upserted: {stats.upserted}, "
        f"Skipped: {stats.skipped}, Errors: {len(stats.errors)}"
    )


if __name__ == "__main__":
    main()
