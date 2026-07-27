from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
import os
from typing import Callable, TypeVar

T = TypeVar("T")

# Use a single worker so Playwright objects that expect thread affinity
# can be reused safely across calls in environments that already run an event loop.
_PLAYWRIGHT_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright-sync")


def run_sync_playwright(callable_obj: Callable[[], T]) -> T:
    if not _has_running_event_loop():
        return callable_obj()

    future: Future[T] = _PLAYWRIGHT_SYNC_EXECUTOR.submit(callable_obj)
    return future.result()


def configured_browser_channel(channel: str | None, *, browser_name: str) -> str | None:
    """Return an explicitly permitted system-browser channel, if any.

    Container deployments use the browser packaged in the pinned Playwright
    image. A system channel such as Google Chrome is only honored when an
    operator opts in, preventing a stale channel setting from coupling the
    worker to an unverified browser install.
    """
    normalized = (channel or "").strip()
    if not normalized or browser_name != "chromium":
        return None
    if not _env_bool("PLAYWRIGHT_ALLOW_SYSTEM_BROWSER_CHANNEL", default=False):
        return None
    return normalized


def _has_running_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
