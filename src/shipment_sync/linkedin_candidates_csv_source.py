from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from .linkedin_candidates_models import CandidateProfile, JobCriteria, JobSearchResult

_PROFILE_PATH_RE = re.compile(r"^/(in|pub)/[^/?#]+")


class LinkedInCsvSource:
    def __init__(self, csv_path: str):
        path = Path(csv_path)
        if not path.exists():
            raise ValueError(f"Input CSV not found: {path}")
        self.path = path
        self._rows = _read_rows(path)

    def search_job(self, job: JobCriteria, *, max_results_override: int | None = None) -> JobSearchResult:
        max_results = max_results_override if max_results_override and max_results_override > 0 else job.max_results
        query_label = f"csv:{self.path.name}"
        candidates: list[CandidateProfile] = []
        seen_urls: set[str] = set()
        rank = 1

        for row in self._rows:
            profile_url = _normalize_linkedin_url(_read_value(row, ["profileurl", "url", "linkedinurl"]))
            if not profile_url:
                continue
            if profile_url in seen_urls:
                continue

            first_name = _read_value(row, ["firstname", "first_name"])
            last_name = _read_value(row, ["lastname", "last_name"])
            full_name = " ".join([p for p in [first_name, last_name] if p]).strip() or "Unknown"

            position = _read_value(row, ["position", "headline", "title"])
            company = _read_value(row, ["company"])
            location = _read_value(row, ["location", "country", "city"])
            connected_on = _read_value(row, ["connectedon", "connected_on", "connectiondate"])

            searchable_parts = [full_name, position, company, location]
            searchable_text = " ".join([part for part in searchable_parts if part]).lower()
            score = _score_candidate(searchable_text, rank=rank, job=job)
            if score <= 0:
                rank += 1
                continue

            headline = _build_headline(position, company)
            snippet = _build_snippet(location, connected_on)
            candidates.append(
                CandidateProfile(
                    linkedin_url=profile_url,
                    full_name=full_name,
                    headline=headline,
                    snippet=snippet,
                    location_hint=_match_location(searchable_text, job.locations) or location,
                    score=score,
                    first_seen_rank=rank,
                    job_ids=[job.job_id],
                    job_names=[job.job_name],
                    source_queries=[query_label],
                )
            )
            seen_urls.add(profile_url)
            rank += 1
            if len(candidates) >= max_results:
                break

        return JobSearchResult(job=job, candidates=candidates, queries=[query_label])


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV has no header row: {path}")
        rows: list[dict[str, str]] = []
        for row in reader:
            if not isinstance(row, dict):
                continue
            cleaned: dict[str, str] = {}
            for key, value in row.items():
                if key is None:
                    continue
                normalized_key = _normalize_token(key)
                cleaned[normalized_key] = str(value or "").strip()
            if cleaned:
                rows.append(cleaned)
        return rows


def _read_value(row: dict[str, str], keys: list[str]) -> str | None:
    for key in keys:
        value = row.get(_normalize_token(key), "").strip()
        if value:
            return value
    return None


def _build_headline(position: str | None, company: str | None) -> str | None:
    if position and company:
        return f"{position} at {company}"
    return position or company


def _build_snippet(location: str | None, connected_on: str | None) -> str | None:
    bits: list[str] = []
    if location:
        bits.append(f"Location: {location}")
    if connected_on:
        bits.append(f"Connected on: {connected_on}")
    if not bits:
        return None
    return " | ".join(bits)


def _score_candidate(searchable_text: str, *, rank: int, job: JobCriteria) -> int:
    title_hits = sum(1 for token in job.titles if token.lower() in searchable_text)
    skill_hits = sum(1 for token in job.skills if token.lower() in searchable_text)
    include_hits = sum(1 for token in job.include_keywords if token.lower() in searchable_text)
    exclude_hits = sum(1 for token in job.exclude_keywords if token.lower() in searchable_text)

    if title_hits == 0 and skill_hits == 0:
        return 0

    score = 50
    score += title_hits * 12
    score += skill_hits * 7
    score += include_hits * 4
    score -= exclude_hits * 25
    score -= max(0, rank // 3)

    if score <= 0:
        return 0
    return min(100, score)


def _match_location(searchable_text: str, locations: list[str]) -> str | None:
    for location in locations:
        if location.lower() in searchable_text:
            return location
    return None


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _normalize_linkedin_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    try:
        parsed = urlparse(raw_url.strip())
    except Exception:
        return None

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "linkedin.com":
        return None
    if not _PROFILE_PATH_RE.match(parsed.path):
        return None

    normalized = parsed._replace(
        scheme="https",
        netloc="www.linkedin.com",
        path=parsed.path.rstrip("/"),
        params="",
        query="",
        fragment="",
    )
    return urlunparse(normalized)

