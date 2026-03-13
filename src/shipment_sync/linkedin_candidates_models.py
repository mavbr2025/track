from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class JobCriteria:
    job_id: str
    job_name: str
    titles: list[str]
    locations: list[str]
    skills: list[str]
    include_keywords: list[str]
    exclude_keywords: list[str]
    max_results: int


@dataclass
class CandidateProfile:
    linkedin_url: str
    full_name: str
    headline: str | None
    snippet: str | None
    location_hint: str | None
    score: int
    first_seen_rank: int
    job_ids: list[str] = field(default_factory=list)
    job_names: list[str] = field(default_factory=list)
    source_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class JobSearchResult:
    job: JobCriteria
    candidates: list[CandidateProfile]
    queries: list[str]

