from datetime import datetime, timedelta
import logging
import threading
import time
from typing import Any, Dict, Optional

from app.workers.jobs import run_trending_refresh_job
from app.workers.worker_config import WorkerConfig, get_worker_config

logger = logging.getLogger("app.workers.scheduler")


class BackgroundScheduler:
    """Lightweight background thread scheduler for periodic trending discovery and topic refresh."""

    def __init__(self, config: Optional[WorkerConfig] = None):
        self.config = config or get_worker_config()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_run_time: Optional[datetime] = None
        self.last_run_result: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.total_runs: int = 0

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
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(auto_discover, run_immediately),
                name="VantageBackgroundScheduler",
                daemon=True,
            )
            self._thread.start()
            logger.info("BackgroundScheduler started (interval=%.1fh)", self.config.worker_interval_hours)

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Signal the scheduler thread to stop and wait for it to join."""
        with self._lock:
            if not self.is_running:
                return

            self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=timeout_seconds)
            logger.info("BackgroundScheduler stopped gracefully.")

    def trigger_now(
        self,
        auto_discover: bool = False,
        force_refresh_all: bool = False,
    ) -> Dict[str, Any]:
        """Trigger an immediate execution cycle in the calling thread."""
        logger.info("Triggering immediate topic refresh job")
        try:
            result = run_trending_refresh_job(
                auto_discover=auto_discover,
                force_refresh_all=force_refresh_all,
            )
            with self._lock:
                self.last_run_time = datetime.utcnow()
                self.last_run_result = result
                self.last_error = None
                self.total_runs += 1
            return result
        except Exception as e:
            logger.error("Error during manual trigger: %s", str(e))
            with self._lock:
                self.last_error = str(e)
            raise

    def _run_loop(self, auto_discover: bool, run_immediately: bool) -> None:
        """Main loop executed by the background thread."""
        interval_seconds = max(10.0, self.config.worker_interval_hours * 3600.0)

        if run_immediately:
            try:
                self.trigger_now(auto_discover=auto_discover)
            except Exception as e:
                logger.error("Error in initial scheduler execution: %s", str(e))

        while not self._stop_event.is_set():
            # Wait with frequent checks to allow responsive termination
            sleep_step = 1.0
            elapsed = 0.0
            while elapsed < interval_seconds and not self._stop_event.is_set():
                time.sleep(sleep_step)
                elapsed += sleep_step

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
        }


# Global singleton instance for easy import in FastAPI lifecycle
scheduler = BackgroundScheduler()
