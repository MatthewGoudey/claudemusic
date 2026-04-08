"""
Prefect flow for album tracklist resolution via MusicBrainz.

Can run independently or after the ingestion flow.

Usage:
    # Standalone
    python -m flows.resolve_flow

    # Deploy with schedule (run daily at 3am)
    prefect deployment build flows/resolve_flow.py:tracklist_resolution_flow \
        --name scheduled \
        --cron "0 3 * * *" \
        --apply
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from prefect import flow, task, get_run_logger

logger = logging.getLogger(__name__)


@task(
    name="resolve-tracklists",
    retries=2,
    retry_delay_seconds=[60, 300],
)
def resolve_tracklists_task() -> dict:
    """Resolve all albums missing from album_tracklist via MusicBrainz.

    This task runs synchronously (Prefect tasks are sync by default)
    but uses asyncio.run() internally for the async resolver.
    """
    run_logger = get_run_logger()

    # Import here to avoid circular imports and ensure dotenv is loaded
    from src.musicbrainz import resolve_all_missing

    run_logger.info("Starting tracklist resolution...")
    result = asyncio.run(resolve_all_missing())

    run_logger.info(
        f"Resolution complete — "
        f"resolved: {result['resolved']}, "
        f"failed: {result['failed']}, "
        f"already_cached: {result['already_cached']}"
    )

    if result["failures"]:
        run_logger.warning(f"Failed albums (first 10): {result['failures'][:10]}")

    return result


@flow(
    name="tracklist-resolution",
    description="Resolve album tracklist sizes from MusicBrainz for album completion calculations.",
    log_prints=True,
)
def tracklist_resolution_flow() -> dict:
    """Main flow for tracklist resolution."""
    run_logger = get_run_logger()
    start = datetime.now(timezone.utc)
    run_logger.info(f"Flow started at {start.isoformat()}")

    result = resolve_tracklists_task()

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    run_logger.info(f"Flow complete in {elapsed:.1f}s")

    return result


if __name__ == "__main__":
    print("Running tracklist resolution flow...")
    result = tracklist_resolution_flow()
    print(f"Done: {result}")
