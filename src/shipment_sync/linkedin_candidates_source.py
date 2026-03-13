from __future__ import annotations

from dataclasses import replace
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

from .linkedin_candidates_config import LinkedInCandidateSettings
from .linkedin_candidates_models import CandidateProfile, JobCriteria, JobSearchResult

_PROFILE_PATH_RE = re.compile(r"^/(in|pub)/[^/?#]+")


class GoogleCustomSearchLinkedInSource:
    def __init__(self, settings: LinkedInCandidateSettings):
        self.settings = settings
        self.session = requests.Session()
        self.endpoint = "https://www.googleapis.com/customsearch/v1"

    def search_job(self, job: JobCriteria, *, max_results_override: int | None = None) -> JobSearchResult:
        max_results = max_results_override if max_results_override and max_results_override > 0 else job.max_results
        queries = _build_queries(job)

        candidates: list[CandidateProfile] = []
        seen_urls: set[str] = set()
        rank = 1
        for query in queries:
            if len(candidates) >= max_results:
                break
            remaining = max_results - len(candidates)
            for item in self._search_query(query, max_results=remaining):
                candidate = _item_to_candidate(item, job=job, query=query, rank=rank)
                rank += 1
                if candidate is None:
                    continue
                if candidate.linkedin_url in seen_urls:
                    continue
                candidates.append(candidate)
                seen_urls.add(candidate.linkedin_url)
                if len(candidates) >= max_results:
                    break

        return JobSearchResult(job=job, candidates=candidates, queries=queries)

    def _search_query(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start = 1

        while len(results) < max_results and start <= 91:
            count = min(10, max_results - len(results))
            params = {
                "key": self.settings.google_cse_api_key,
                "cx": self.settings.google_cse_engine_id,
                "q": query,
                "num": str(count),
                "start": str(start),
            }
            response = self.session.get(self.endpoint, params=params, timeout=self.settings.google_cse_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("items", [])
            if not isinstance(batch, list) or not batch:
                break
            dict_batch = [item for item in batch if isinstance(item, dict)]
            if not dict_batch:
                break
            results.extend(dict_batch)
            if len(dict_batch) < count:
                break
            start += len(dict_batch)

        return results


def merge_job_candidates(job_results: list[JobSearchResult]) -> list[CandidateProfile]:
    merged: dict[str, CandidateProfile] = {}
    for result in job_results:
        for candidate in result.candidates:
            existing = merged.get(candidate.linkedin_url)
            if existing is None:
                merged[candidate.linkedin_url] = replace(candidate)
                continue
            if candidate.score > existing.score:
                existing.score = candidate.score
            if candidate.first_seen_rank < existing.first_seen_rank:
                existing.first_seen_rank = candidate.first_seen_rank
            if not existing.headline and candidate.headline:
                existing.headline = candidate.headline
            if not existing.snippet and candidate.snippet:
                existing.snippet = candidate.snippet
            if not existing.location_hint and candidate.location_hint:
                existing.location_hint = candidate.location_hint

            for value in candidate.job_ids:
                if value not in existing.job_ids:
                    existing.job_ids.append(value)
            for value in candidate.job_names:
                if value not in existing.job_names:
                    existing.job_names.append(value)
            for value in candidate.source_queries:
                if value not in existing.source_queries:
                    existing.source_queries.append(value)

    return sorted(
        merged.values(),
        key=lambda c: (-c.score, c.first_seen_rank, c.full_name.lower(), c.linkedin_url),
    )


def _item_to_candidate(item: dict[str, Any], *, job: JobCriteria, query: str, rank: int) -> CandidateProfile | None:
    raw_link = str(item.get("link") or "").strip()
    linkedin_url = _normalize_linkedin_url(raw_link)
    if not linkedin_url:
        return None

    raw_title = str(item.get("title") or "").strip()
    full_name, headline_from_title = _split_title(raw_title)
    snippet = str(item.get("snippet") or "").strip() or None

    searchable = "\n".join(
        [value for value in [full_name, headline_from_title, snippet] if value]
    ).lower()
    score = _score_candidate(searchable, rank=rank, job=job)
    if score <= 0:
        return None

    return CandidateProfile(
        linkedin_url=linkedin_url,
        full_name=full_name,
        headline=headline_from_title,
        snippet=snippet,
        location_hint=_match_location(searchable, job.locations),
        score=score,
        first_seen_rank=rank,
        job_ids=[job.job_id],
        job_names=[job.job_name],
        source_queries=[query],
    )


def _score_candidate(searchable_text: str, *, rank: int, job: JobCriteria) -> int:
    score = max(0, 100 - rank)

    for token in job.titles:
        if token.lower() in searchable_text:
            score += 7
    for token in job.skills:
        if token.lower() in searchable_text:
            score += 5
    for token in job.include_keywords:
        if token.lower() in searchable_text:
            score += 4
    for token in job.exclude_keywords:
        if token.lower() in searchable_text:
            score -= 18

    if score < 0:
        return 0
    return min(100, score)


def _match_location(searchable_text: str, locations: list[str]) -> str | None:
    for location in locations:
        if location.lower() in searchable_text:
            return location
    return None


def _split_title(raw_title: str) -> tuple[str, str | None]:
    if not raw_title:
        return "Unknown", None
    cleaned = raw_title.replace("| LinkedIn", "").replace("- LinkedIn", "").strip(" -|")
    if " - " in cleaned:
        name, headline = cleaned.split(" - ", 1)
        name_clean = name.strip() or "Unknown"
        headline_clean = headline.strip() or None
        return name_clean, headline_clean
    return cleaned or "Unknown", None


def _build_queries(job: JobCriteria) -> list[str]:
    title_terms = _or_block(job.titles)
    skill_terms = _or_block(job.skills)

    base_parts = ["site:linkedin.com/in"]
    if title_terms:
        base_parts.append(f"({title_terms})")
    if skill_terms:
        base_parts.append(f"({skill_terms})")
    for keyword in job.include_keywords:
        base_parts.append(_quoted(keyword))
    for keyword in job.exclude_keywords:
        base_parts.append(f"-{_quoted(keyword)}")

    base_query = " ".join(base_parts).strip()
    queries: list[str] = []

    if job.locations:
        for location in job.locations:
            location_text = _quoted(location)
            query = f"{base_query} {location_text}".strip()
            if query and query not in queries:
                queries.append(query)
    else:
        queries.append(base_query)

    return queries


def _or_block(values: list[str]) -> str:
    cleaned = [_quoted(value) for value in values if value.strip()]
    if not cleaned:
        return ""
    return " OR ".join(cleaned)


def _quoted(value: str) -> str:
    cleaned = value.replace('"', "").strip()
    if not cleaned:
        return ""
    return f"\"{cleaned}\""


def _normalize_linkedin_url(url: str) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "linkedin.com":
        return None
    if not _PROFILE_PATH_RE.match(parsed.path):
        return None

    normalized_path = parsed.path.rstrip("/")
    normalized = parsed._replace(
        scheme="https",
        netloc="www.linkedin.com",
        path=normalized_path,
        params="",
        query="",
        fragment="",
    )
    return urlunparse(normalized)
