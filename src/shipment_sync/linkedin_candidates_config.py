from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
from typing import Any

from .linkedin_candidates_models import JobCriteria
from .project_paths import REPO_ROOT, local_config_path, resolve_existing_file


@dataclass
class LinkedInCandidateSettings:
    clickup_api_token: str
    clickup_candidates_list_id: str
    clickup_candidate_cf_linkedin: str | None
    clickup_candidate_cf_role: str | None
    clickup_candidate_cf_location: str | None
    clickup_candidate_cf_match_score: str | None
    clickup_candidate_cf_source_query: str | None
    clickup_candidate_task_status: str | None
    google_cse_api_key: str
    google_cse_engine_id: str
    google_cse_timeout_seconds: int
    candidate_default_max_results: int

    @classmethod
    def from_env(
        cls,
        *,
        require_clickup: bool,
        require_google_cse: bool,
    ) -> "LinkedInCandidateSettings":
        clickup_api_token = _must("CLICKUP_API_TOKEN") if require_clickup else _optional("CLICKUP_API_TOKEN") or ""
        clickup_candidates_list_id = (
            _must("CLICKUP_CANDIDATES_LIST_ID")
            if require_clickup
            else _optional("CLICKUP_CANDIDATES_LIST_ID") or ""
        )
        google_cse_api_key = (
            _must("GOOGLE_CSE_API_KEY") if require_google_cse else _optional("GOOGLE_CSE_API_KEY") or ""
        )
        google_cse_engine_id = (
            _must("GOOGLE_CSE_ENGINE_ID") if require_google_cse else _optional("GOOGLE_CSE_ENGINE_ID") or ""
        )

        return cls(
            clickup_api_token=clickup_api_token,
            clickup_candidates_list_id=clickup_candidates_list_id,
            clickup_candidate_cf_linkedin=_optional("CLICKUP_CANDIDATE_CF_LINKEDIN"),
            clickup_candidate_cf_role=_optional("CLICKUP_CANDIDATE_CF_ROLE"),
            clickup_candidate_cf_location=_optional("CLICKUP_CANDIDATE_CF_LOCATION"),
            clickup_candidate_cf_match_score=_optional("CLICKUP_CANDIDATE_CF_MATCH_SCORE"),
            clickup_candidate_cf_source_query=_optional("CLICKUP_CANDIDATE_CF_SOURCE_QUERY"),
            clickup_candidate_task_status=_optional("CLICKUP_CANDIDATE_TASK_STATUS"),
            google_cse_api_key=google_cse_api_key,
            google_cse_engine_id=google_cse_engine_id,
            google_cse_timeout_seconds=_int("GOOGLE_CSE_TIMEOUT_SECONDS", default=30, min_value=5),
            candidate_default_max_results=_int("CANDIDATE_DEFAULT_MAX_RESULTS", default=25, min_value=1),
        )


def load_criteria(
    path: str,
    *,
    default_max_results: int,
    filters: list[str] | None = None,
) -> list[JobCriteria]:
    criteria_path = resolve_existing_file(
        path,
        project_candidates=(
            local_config_path("linkedin_criteria.json"),
            REPO_ROOT / "linkedin_criteria.json",
        ),
    )

    try:
        payload = json.loads(criteria_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON in criteria file: {criteria_path}") from exc

    entries: list[dict[str, Any]]
    if isinstance(payload, list):
        entries = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        raw_jobs = payload.get("jobs", [])
        entries = [item for item in raw_jobs if isinstance(item, dict)]
    else:
        raise ValueError("Criteria file must be a JSON list or an object with a 'jobs' list")

    out: list[JobCriteria] = []
    for idx, raw in enumerate(entries, start=1):
        raw_name = str(raw.get("job_name") or raw.get("name") or "").strip()
        if not raw_name:
            raise ValueError(f"Job criteria entry {idx} is missing 'job_name' or 'name'")

        raw_id = str(raw.get("job_id") or "").strip()
        job_id = raw_id or _slugify(raw_name)

        titles = _to_text_list(raw.get("titles"))
        if not titles:
            single_title = str(raw.get("title") or "").strip()
            if single_title:
                titles = [single_title]
        if not titles:
            raise ValueError(f"Job criteria '{raw_name}' must include at least one title")

        max_results = _coerce_int(raw.get("max_results"), default=default_max_results, min_value=1)
        out.append(
            JobCriteria(
                job_id=job_id,
                job_name=raw_name,
                titles=titles,
                locations=_to_text_list(raw.get("locations")),
                skills=_to_text_list(raw.get("skills")),
                include_keywords=_to_text_list(raw.get("include_keywords")),
                exclude_keywords=_to_text_list(raw.get("exclude_keywords")),
                max_results=max_results,
            )
        )

    if filters:
        tokens = [token.strip().lower() for token in filters if token.strip()]
        if tokens:
            out = [
                job
                for job in out
                if any(token in job.job_id.lower() or token in job.job_name.lower() for token in tokens)
            ]

    if not out:
        raise ValueError("No job criteria matched. Check filters or criteria file.")
    return out


def _must(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required env var: {key}")
    return value


def _optional(key: str) -> str | None:
    value = os.getenv(key)
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def _int(key: str, *, default: int, min_value: int) -> int:
    value = os.getenv(key)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except Exception:
        return default
    return parsed if parsed >= min_value else default


def _coerce_int(value: Any, *, default: int, min_value: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return parsed if parsed >= min_value else default


def _to_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            cleaned = str(item).strip()
            if cleaned:
                out.append(cleaned)
        return out
    return []


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return base or "job"
