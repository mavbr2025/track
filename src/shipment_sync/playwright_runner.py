from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
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


def _has_running_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
