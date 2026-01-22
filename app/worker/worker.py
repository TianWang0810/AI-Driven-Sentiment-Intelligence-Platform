"""
Background worker for processing jobs.

Worker flow (every POLL_INTERVAL):
    1. Watchdog: Release stuck jobs (locked_at < now - LOCK_TIMEOUT)
    2. Claim: Atomic claim using CTE + FOR UPDATE SKIP LOCKED
    3. Process: Run pipeline
    4. Handle success/failure: Update job status

Claiming uses atomic SQL:
    WITH claimable AS (
        SELECT id FROM jobs
        WHERE status = 'queued'
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE jobs SET status = 'running', ...
    FROM claimable WHERE jobs.id = claimable.id
    RETURNING jobs.*
"""

import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from ..config import get_settings
from ..extensions import get_db_context
from ..logging_config import (
    Timer,
    bind_context,
    clear_context,
    get_logger,
    setup_logging,
)
from ..models import Job, JobStatus
from ..services.errors import PipelineError
from ..services.pipeline import run_pipeline

logger = get_logger(__name__)


class Worker:
    """
    Background worker that processes jobs from the database queue.

    Features:
        - Atomic job claiming with FOR UPDATE SKIP LOCKED
        - Watchdog for releasing stuck jobs (concurrent-safe)
        - Graceful shutdown on SIGTERM/SIGINT
        - Proper success/failure handling with DB commits
    """

    def __init__(self, worker_id: Optional[str] = None):
        """
        Initialize worker.

        Args:
            worker_id: Unique worker identifier
        """
        self.settings = get_settings()
        self.worker_id = worker_id or self.settings.worker_id
        self.running = False
        self.current_job_id: Optional[int] = None

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown on SIGTERM/SIGINT."""
        logger.info(
            "shutdown_signal_received",
            signal=signum,
            worker_id=self.worker_id,
        )
        self.running = False

        if self.current_job_id:
            logger.info("waiting_for_current_job", job_id=self.current_job_id)

    def _run_watchdog(self) -> int:
        """
        Release stuck jobs (locked too long) using atomic SQL updates.

        Jobs with status='running' and locked_at < now - LOCK_TIMEOUT:
            - If attempts < max_attempts: Reset to 'queued', set error_code='LOCK_TIMEOUT'
            - Else: Mark as 'failed'

        This implementation is concurrent-safe - uses atomic UPDATE statements
        instead of SELECT-then-UPDATE pattern.

        Returns:
            Number of jobs released
        """
        lock_timeout = self.settings.worker_lock_timeout
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=lock_timeout)
        now = datetime.now(timezone.utc)
        released = 0

        with get_db_context() as session:
            # Atomic UPDATE for retryable stuck jobs (attempts < max_attempts)
            # Resets status to 'queued' so they can be picked up again
            retry_sql = text("""
                UPDATE jobs
                SET status = 'queued',
                    locked_at = NULL,
                    locked_by = NULL,
                    error_code = 'LOCK_TIMEOUT',
                    error_message = :error_message,
                    updated_at = :now
                WHERE status = 'running'
                  AND locked_at < :cutoff
                  AND attempts < max_attempts
                RETURNING id, attempts
            """)

            result = session.execute(
                retry_sql,
                {
                    "error_message": f"Job was locked for more than {lock_timeout}s",
                    "now": now,
                    "cutoff": cutoff,
                },
            )
            retried_jobs = result.fetchall()

            for row in retried_jobs:
                logger.warning(
                    "watchdog_released_stuck_job",
                    job_id=row[0],
                    attempts=row[1],
                    action="retry",
                )
            released += len(retried_jobs)

            # Atomic UPDATE for failed stuck jobs (attempts >= max_attempts)
            # Marks as 'failed' since no more retries are allowed
            fail_sql = text("""
                UPDATE jobs
                SET status = 'failed',
                    locked_at = NULL,
                    locked_by = NULL,
                    error_code = 'LOCK_TIMEOUT',
                    error_message = :error_message,
                    completed_at = :now,
                    updated_at = :now
                WHERE status = 'running'
                  AND locked_at < :cutoff
                  AND attempts >= max_attempts
                RETURNING id, attempts
            """)

            result = session.execute(
                fail_sql,
                {
                    "error_message": "Job failed after max attempts (stuck)",
                    "now": now,
                    "cutoff": cutoff,
                },
            )
            failed_jobs = result.fetchall()

            for row in failed_jobs:
                logger.error(
                    "watchdog_marked_job_failed",
                    job_id=row[0],
                    attempts=row[1],
                )
            released += len(failed_jobs)

            # get_db_context commits automatically on exit

        if released > 0:
            logger.info("watchdog_complete", released=released)

        return released

    def _claim_job(self) -> Optional[Job]:
        """
        Atomically claim a job using CTE + FOR UPDATE SKIP LOCKED.

        Uses a single atomic SQL statement to:
            1. Find the oldest queued job that can be retried
            2. Lock it (SKIP LOCKED ensures no contention)
            3. Update status to 'running' and set lock info
            4. Return all job fields (avoids second SELECT)

        Returns:
            Claimed Job object or None if no jobs available
        """
        now = datetime.now(timezone.utc)

        # Atomic claim: CTE selects + locks, UPDATE modifies, RETURNING avoids second query
        claim_sql = text("""
            WITH claimable AS (
                SELECT id FROM jobs
                WHERE status = 'queued'
                  AND attempts < max_attempts
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE jobs
            SET status = 'running',
                locked_by = :worker_id,
                locked_at = :now,
                attempts = attempts + 1,
                updated_at = :now,
                started_at = COALESCE(started_at, :now)
            FROM claimable
            WHERE jobs.id = claimable.id
            RETURNING jobs.id, jobs.source_type, jobs.source_url, jobs.options,
                      jobs.status, jobs.attempts, jobs.max_attempts, jobs.progress,
                      jobs.locked_at, jobs.locked_by, jobs.error_code, jobs.error_message,
                      jobs.stats, jobs.created_at, jobs.updated_at, jobs.started_at,
                      jobs.completed_at
        """)

        with get_db_context() as session:
            result = session.execute(
                claim_sql,
                {
                    "worker_id": self.worker_id,
                    "now": now,
                },
            )
            row = result.fetchone()

            if not row:
                return None

            # Construct Job object directly from RETURNING clause
            # This avoids a second SELECT query
            job = Job(
                id=row[0],
                source_type=row[1],
                source_url=row[2],
                options=row[3],
                status=JobStatus(row[4]),
                attempts=row[5],
                max_attempts=row[6],
                progress=row[7],
                locked_at=row[8],
                locked_by=row[9],
                error_code=row[10],
                error_message=row[11],
                stats=row[12],
                created_at=row[13],
                updated_at=row[14],
                started_at=row[15],
                completed_at=row[16],
            )

            logger.info(
                "job_claimed",
                job_id=job.id,
                attempt=job.attempts,
                worker_id=self.worker_id,
            )

            # get_db_context commits automatically on exit
            return job

    def _handle_success(
        self,
        job: Job,
        progress: int = 0,
        stats: Optional[dict] = None,
    ) -> None:
        """
        Handle job success - mark as SUCCEEDED in database.

        Args:
            job: The job that succeeded
            progress: Number of items processed
            stats: Optional statistics dictionary
        """
        now = datetime.now(timezone.utc)

        with get_db_context() as session:
            success_sql = text("""
                UPDATE jobs
                SET status = 'succeeded',
                    locked_at = NULL,
                    locked_by = NULL,
                    progress = :progress,
                    stats = :stats,
                    completed_at = :now,
                    updated_at = :now,
                    error_code = NULL,
                    error_message = NULL
                WHERE id = :job_id
            """)

            session.execute(
                success_sql,
                {
                    "job_id": job.id,
                    "progress": progress,
                    "stats": stats,
                    "now": now,
                },
            )

            # get_db_context commits automatically on exit

        logger.info(
            "job_marked_succeeded",
            job_id=job.id,
            progress=progress,
        )

    def _handle_failure(self, job: Job, error: PipelineError) -> None:
        """
        Handle job failure - retry or mark as failed.

        If retryable and attempts < max_attempts:
            Reset to 'queued' for another attempt
        Else:
            Mark as 'failed' permanently

        Args:
            job: The job that failed
            error: The PipelineError with code, message, and retryable flag
        """
        now = datetime.now(timezone.utc)

        with get_db_context() as session:
            if error.retryable and job.attempts < job.max_attempts:
                # Reset for retry - will be picked up by next claim cycle
                retry_sql = text("""
                    UPDATE jobs
                    SET status = 'queued',
                        locked_at = NULL,
                        locked_by = NULL,
                        error_code = :error_code,
                        error_message = :error_message,
                        updated_at = :now
                    WHERE id = :job_id
                """)

                session.execute(
                    retry_sql,
                    {
                        "job_id": job.id,
                        "error_code": error.code,
                        "error_message": error.message,
                        "now": now,
                    },
                )

                logger.warning(
                    "job_scheduled_for_retry",
                    job_id=job.id,
                    attempt=job.attempts,
                    error_code=error.code,
                    retryable=True,
                )
            else:
                # Mark as permanently failed
                fail_sql = text("""
                    UPDATE jobs
                    SET status = 'failed',
                        locked_at = NULL,
                        locked_by = NULL,
                        error_code = :error_code,
                        error_message = :error_message,
                        completed_at = :now,
                        updated_at = :now
                    WHERE id = :job_id
                """)

                session.execute(
                    fail_sql,
                    {
                        "job_id": job.id,
                        "error_code": error.code,
                        "error_message": error.message,
                        "now": now,
                    },
                )

                logger.error(
                    "job_failed",
                    job_id=job.id,
                    attempt=job.attempts,
                    error_code=error.code,
                    retryable=error.retryable,
                )

            # get_db_context commits automatically on exit

    def _process_job(self, job: Job) -> bool:
        """
        Process a single job through the pipeline.

        Runs the pipeline and handles success/failure appropriately.

        Args:
            job: Job to process

        Returns:
            True if successful, False otherwise
        """
        self.current_job_id = job.id
        bind_context(job_id=job.id, worker_id=self.worker_id, attempt=job.attempts)

        try:
            with Timer() as timer:
                result = run_pipeline(job, use_llm=self.settings.has_openai_key)

            # Extract progress and stats from pipeline result if available
            progress = 0
            stats = None

            if isinstance(result, dict):
                progress = result.get("progress", 0)
                stats = result.get("stats")
            elif isinstance(result, int):
                progress = result

            # Mark job as succeeded in database
            self._handle_success(job, progress=progress, stats=stats)

            logger.info(
                "job_processed_successfully",
                job_id=job.id,
                latency_ms=timer.elapsed_ms,
                progress=progress,
            )
            return True

        except PipelineError as e:
            self._handle_failure(job, e)
            return False

        except Exception as e:
            # Wrap unexpected errors in UnknownError
            from ..services.errors import UnknownError

            self._handle_failure(job, UnknownError(e))
            return False

        finally:
            self.current_job_id = None
            clear_context()

    def run(self) -> None:
        """
        Main worker loop.

        Continuously:
            1. Run watchdog to release stuck jobs
            2. Try to claim a job
            3. If claimed, process it
            4. If no jobs, sleep for poll_interval

        Runs until shutdown signal received.
        """
        logger.info(
            "worker_started",
            worker_id=self.worker_id,
            poll_interval=self.settings.worker_poll_interval,
            lock_timeout=self.settings.worker_lock_timeout,
        )

        self.running = True
        jobs_processed = 0
        jobs_failed = 0

        while self.running:
            try:
                # Step 1: Watchdog - release stuck jobs (concurrent-safe)
                self._run_watchdog()

                # Step 2: Try to claim a job atomically
                job = self._claim_job()

                if job:
                    # Step 3: Process the claimed job
                    success = self._process_job(job)

                    if success:
                        jobs_processed += 1
                    else:
                        jobs_failed += 1

                    # Small delay between jobs to prevent CPU spin
                    time.sleep(0.5)
                else:
                    # No jobs available, wait before polling again
                    time.sleep(self.settings.worker_poll_interval)

            except Exception as e:
                # Catch-all for unexpected errors in the main loop
                logger.error(
                    "worker_loop_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                time.sleep(self.settings.worker_poll_interval)

        logger.info(
            "worker_stopped",
            worker_id=self.worker_id,
            jobs_processed=jobs_processed,
            jobs_failed=jobs_failed,
        )


def run_worker(worker_id: Optional[str] = None) -> None:
    """
    Entry point for running the worker.

    Sets up logging, initializes database, and starts the worker loop.

    Args:
        worker_id: Optional unique worker identifier
    """
    # Setup structured logging
    setup_logging()

    logger.info("initializing_worker")

    # Initialize database tables if needed
    from ..extensions import init_db

    init_db()

    # Create and run worker
    worker = Worker(worker_id=worker_id)
    worker.run()


if __name__ == "__main__":
    # Allow passing worker_id as command line argument
    worker_id = sys.argv[1] if len(sys.argv) > 1 else None
    run_worker(worker_id)


__all__ = ["Worker", "run_worker"]