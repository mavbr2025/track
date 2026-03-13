from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
ARTIFACTS_BUILD_DIR = ARTIFACTS_DIR / "build"
ARTIFACTS_OUTPUT_DIR = ARTIFACTS_DIR / "output"
CONFIG_DIR = REPO_ROOT / "config"
LOCAL_CONFIG_DIR = CONFIG_DIR / "local"
EXAMPLE_CONFIG_DIR = CONFIG_DIR / "examples"


def local_config_path(filename: str) -> Path:
    return LOCAL_CONFIG_DIR / filename


def example_config_path(filename: str) -> Path:
    return EXAMPLE_CONFIG_DIR / filename


def artifacts_output_path(*parts: str) -> Path:
    return ARTIFACTS_OUTPUT_DIR.joinpath(*parts)


def artifacts_build_path(*parts: str) -> Path:
    return ARTIFACTS_BUILD_DIR.joinpath(*parts)


def resolve_existing_file(path_text: str, *, project_candidates: Iterable[Path] = ()) -> Path:
    for candidate in _candidate_paths(path_text, project_candidates=project_candidates):
        if candidate.exists():
            return candidate
    raise ValueError(f"File not found: {path_text}")


def resolve_optional_existing_file(
    path_text: str,
    *,
    project_candidates: Iterable[Path] = (),
    allow_missing_explicit: bool = False,
) -> Path | None:
    if path_text.strip():
        for candidate in _candidate_paths(path_text, project_candidates=project_candidates):
            if candidate.exists():
                return candidate
        if allow_missing_explicit:
            return None
        raise ValueError(f"File not found: {path_text}")

    for candidate in _dedupe(project_candidates):
        if candidate.exists():
            return candidate
    return None


def _candidate_paths(path_text: str, *, project_candidates: Iterable[Path]) -> list[Path]:
    raw = path_text.strip()
    if not raw:
        return list(_dedupe(project_candidates))

    requested = Path(raw).expanduser()
    candidates: list[Path] = []
    if requested.is_absolute():
        candidates.append(requested)
    else:
        candidates.append(requested)
        candidates.append(REPO_ROOT / requested)
        if requested.parent == Path("."):
            candidates.extend(project_candidates)

    return list(_dedupe(candidates))


def _dedupe(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield resolved
