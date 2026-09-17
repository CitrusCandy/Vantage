from datetime import datetime, timedelta
import logging
import threading
import time
from typing import Any, Dict, Optional

from app.core.resilience import CancellationToken
from app.workers.jobs import run_trending_refresh_job
from app.workers.worker_config import WorkerConfig, get_worker_config

logger = logging.getLogger("app.workers.scheduler")


def _get_concurrency_governor():
    """Lazy import to avoid circular dependency."""
    try:
        from app.core import resource_governor
        return resource_governor.concurrency_governor
    except (ImportError, AttributeError):
        return None


class BackgroundScheduler:
    """Lightweight background thread scheduler for periodic trending discovery and topic refresh."""

    def __init__(self, config: Optional[WorkerConfig] = None):
        self.config = config or get_worker_config()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.cancellation_token = CancellationToken()
        self.last_run_time: Optional[datetime] = None
        self.last_run_result: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.total_runs: int = 0
        self.last_backup_time: Optional[datetime] = None
        self.last_backup_status: Optional[str] = None
        self.last_backup_error: Optional[str] = None
        self.total_backups: int = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, auto_discover: bool = False, run_immediately: bool = False) -> None:
        """Start the background scheduler thread if not already running."""
        with self._lock:
            if self.is_running:
                logger.warning("BackgroundScheduler is already running.")
                return

            self._stop_event.clear()
            self.cancellation_token = CancellationToken()

            # Startup orphan recovery
            try:
                from app.database.backup import release_backup_lock
                release_backup_lock()
            except Exception as e:
                logger.debug("Startup lock cleanup: %s", e)

            self._thread = threading.Thread(
                target=self._run_loop,
                args=(auto_discover, run_immediately),
                name="VantageBackgroundScheduler",
                daemon=True,
            )
            self._thread.start()
            logger.info("BackgroundScheduler started (interval=%.1fh)", self.config.worker_interval_hours)

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Signal the scheduler thread to stop, cancel in-flight token, and wait for it to join."""
        with self._lock:
            if not self.is_running:
                return

            self._stop_event.set()
            self.cancellation_token.cancel("scheduler shutdown")

        if self._thread:
            self._thread.join(timeout=timeout_seconds)
            logger.info("BackgroundScheduler stopped gracefully.")

        # Release all held concurrency slots on shutdown
        gov = _get_concurrency_governor()
        if gov:
            gov.release_all()

        # Release backup lock if held
        try:
            from app.database.backup import release_backup_lock
            release_backup_lock()
        except Exception:
            pass

    def run_scheduled_backup(self) -> Optional[Dict[str, Any]]:
        """Execute a scheduled database backup with fail-soft isolation."""
        try:
            from app.database.backup import BackupConfig, acquire_backup_lock, release_backup_lock, create_backup, prune_backups
            config = BackupConfig()
            if not config.enabled:
                return None

            if not acquire_backup_lock():
                logger.warning("Scheduled backup skipped: another backup job is currently in progress")
                return None

            try:
                logger.info("Starting scheduled database backup")
                res = create_backup(config=config)
                # Run retention cleanup
                prune_backups(config=config)
                
                with self._lock:
                    self.last_backup_time = datetime.utcnow()
                    self.last_backup_status = res.get("status", "unknown")
                    self.last_backup_error = res.get("error_type")
                    self.total_backups += 1
                return res
            finally:
                release_backup_lock()
        except Exception as e:
            err_msg = str(e)
            logger.error("Fail-soft scheduled backup error: %s", err_msg)
            with self._lock:
                self.last_backup_time = datetime.utcnow()
                self.last_backup_status = "failed"
                self.last_backup_error = err_msg[:255]
            return None

    def trigger_now(
        self,
        auto_discover: bool = False,
        force_refresh_all: bool = False,
    ) -> Dict[str, Any]:
        """Trigger an immediate execution cycle in the calling thread and persist history."""
        logger.info("Triggering immediate topic refresh job")
        t_start = time.perf_counter()
        start_dt = datetime.utcnow()
        try:
            result = run_trending_refresh_job(
                auto_discover=auto_discover,
                force_refresh_all=force_refresh_all,
            )
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            completed_dt = datetime.utcnow()

            with self._lock:
                self.last_run_time = completed_dt
                self.last_run_result = result
                self.last_error = None
                self.total_runs += 1

            self._persist_cycle(
                started_at=start_dt,
                completed_at=completed_dt,
                duration_ms=duration_ms,
                topics_considered=result.get("total_topics_evaluated", 0),
                topics_refreshed=result.get("refreshed_topics", 0),
                topics_skipped=result.get("skipped_stagnant_topics", 0),
                topics_failed=len(result.get("errors", [])),
                status="success",
            )
            return result
        except Exception as e:
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            completed_dt = datetime.utcnow()
            err_msg = str(e)
            logger.error("Error during manual trigger: %s", err_msg)
            with self._lock:
                self.last_error = err_msg

            self._persist_cycle(
                started_at=start_dt,
                completed_at=completed_dt,
                duration_ms=duration_ms,
                status="failed",
                error_summary=err_msg[:255],
            )
            raise

    def _persist_cycle(
        self,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: float,
        topics_considered: int = 0,
        topics_refreshed: int = 0,
        topics_skipped: int = 0,
        topics_failed: int = 0,
        status: str = "success",
        error_summary: Optional[str] = None,
    ):
        """Persist worker cycle execution to the database safely."""
        try:
            from app.core.security import mask_sensitive_data
            from app.database.database import SessionLocal
            from app.database.models import WorkerCycle

            db = SessionLocal()
            try:
                cycle = WorkerCycle(
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    topics_considered=topics_considered,
                    topics_refreshed=topics_refreshed,
                    topics_skipped=topics_skipped,
                    topics_failed=topics_failed,
                    status=status,
                    error_summary=mask_sensitive_data(error_summary) if error_summary else None,
                )
                db.add(cycle)
                db.commit()
            except Exception as db_err:
                db.rollback()
                logger.warning("Failed to persist WorkerCycle to database: %s", db_err)
            finally:
                db.close()
        except Exception as outer_err:
            logger.warning("WorkerCycle persistence skipped: %s", outer_err)

    def _run_loop(self, auto_discover: bool, run_immediately: bool) -> None:
        """Main loop executed by the background thread."""
        interval_seconds = max(10.0, self.config.worker_interval_hours * 3600.0)
        from app.database.backup import BackupConfig
        backup_cfg = BackupConfig()
        backup_interval_seconds = max(60.0, backup_cfg.interval_hours * 3600.0)
        last_backup_check = time.time()

        if run_immediately:
            try:
                self.trigger_now(auto_discover=auto_discover)
            except Exception as e:
                logger.error("Error in initial scheduler execution: %s", str(e))
            if backup_cfg.enabled:
                self.run_scheduled_backup()

        while not self._stop_event.is_set():
            # Wait with frequent checks to allow responsive termination
            sleep_step = 1.0
            elapsed = 0.0
            while elapsed < interval_seconds and not self._stop_event.is_set():
                time.sleep(sleep_step)
                elapsed += sleep_step

                # Periodic check for scheduled backup
                if time.time() - last_backup_check >= backup_interval_seconds:
                    last_backup_check = time.time()
                    self.run_scheduled_backup()

            if self._stop_event.is_set():
                break

            try:
                self.trigger_now(auto_discover=auto_discover)
            except Exception as e:
                logger.error("Periodic scheduler execution failed: %s", str(e))

    def get_status(self) -> Dict[str, Any]:
        """Return operational status and metadata of the scheduler."""
        next_run = None
        if self.is_running and self.last_run_time:
            interval_delta = timedelta(hours=self.config.worker_interval_hours)
            next_run = (self.last_run_time + interval_delta).isoformat()

        return {
            "is_running": self.is_running,
            "total_runs": self.total_runs,
            "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
            "next_scheduled_run": next_run,
            "worker_interval_hours": self.config.worker_interval_hours,
            "last_error": self.last_error,
            "last_run_result": self.last_run_result,
            "backup_scheduler": {
                "total_backups": self.total_backups,
                "last_backup_time": self.last_backup_time.isoformat() if self.last_backup_time else None,
                "last_backup_status": self.last_backup_status,
                "last_backup_error": self.last_backup_error,
            },
        }


# Global singleton instance for easy import in FastAPI lifecycle
scheduler = BackgroundScheduler()
