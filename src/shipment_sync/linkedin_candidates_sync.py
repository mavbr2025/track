from __future__ import annotations

from dataclasses import dataclass, field

from .clickup_candidates_client import ClickUpCandidatesClient
from .linkedin_candidates_models import CandidateProfile


@dataclass
class LinkedInCandidateSyncStats:
    total_candidates: int
    to_create: int
    created: int
    skipped_existing: int
    errors: list[str] = field(default_factory=list)


def sync_candidates_to_clickup(
    clickup_client: ClickUpCandidatesClient,
    candidates: list[CandidateProfile],
    *,
    dry_run: bool,
) -> LinkedInCandidateSyncStats:
    existing_urls = clickup_client.list_existing_candidate_urls()
    to_create = 0
    created = 0
    skipped_existing = 0
    errors: list[str] = []

    for candidate in candidates:
        if candidate.linkedin_url in existing_urls:
            skipped_existing += 1
            continue

        to_create += 1
        if dry_run:
            existing_urls.add(candidate.linkedin_url)
            continue

        try:
            clickup_client.create_candidate_task(candidate)
            created += 1
            existing_urls.add(candidate.linkedin_url)
        except Exception as exc:
            errors.append(f"{candidate.linkedin_url}: {exc}")

    return LinkedInCandidateSyncStats(
        total_candidates=len(candidates),
        to_create=to_create,
        created=created,
        skipped_existing=skipped_existing,
        errors=errors,
    )

