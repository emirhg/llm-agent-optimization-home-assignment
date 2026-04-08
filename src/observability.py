"""Lightweight logging and in-process metrics for the analytics pipeline."""

from __future__ import annotations

import logging
import sys
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("analytics_pipeline")


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent basic logging setup for CLI scripts and local runs."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

# In-process counters (sufficient for local runs and demos).
_metrics: Counter[str] = Counter()
_status_histogram: Counter[str] = Counter()


def new_request_id() -> str:
    return str(uuid.uuid4())


@contextmanager
def span(name: str, **extra: Any) -> Iterator[None]:
    """Log duration of a logical stage (tracing-lite)."""
    start = time.perf_counter()
    logger.debug("span_start name=%s %s", name, extra)
    try:
        yield
    finally:
        ms = (time.perf_counter() - start) * 1000
        logger.debug("span_end name=%s duration_ms=%.2f", name, ms)


def record_pipeline_outcome(status: str) -> None:
    _metrics["pipeline_runs"] += 1
    _status_histogram[status] += 1


def snapshot_metrics() -> dict[str, Any]:
    return {
        "pipeline_runs": int(_metrics["pipeline_runs"]),
        "by_status": dict(_status_histogram),
    }
