"""
Process and thread lifecycle management for improved stability.
Tracks background tasks, cleans up resources, and prevents leaks.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ThreadInfo:
    """Information about a tracked thread"""
    thread_id: int
    name: str
    started_at: float
    run_id: str
    task_type: str
    is_daemon: bool
    last_seen_at: float = field(default_factory=time.time)


class ThreadTracker:
    """
    Tracks background threads and provides cleanup capabilities
    """

    def __init__(self, state_dir: Path | None = None):
        self.state_dir = state_dir
        self._threads: dict[int, ThreadInfo] = {}
        self._lock = threading.Lock()
        self._cleanup_threshold_seconds = int(os.environ.get("THREAD_CLEANUP_THRESHOLD_SECONDS", "3600"))

    def register(
        self,
        thread: threading.Thread,
        run_id: str,
        task_type: str,
    ) -> None:
        """Register a thread for tracking"""
        with self._lock:
            self._threads[thread.ident or 0] = ThreadInfo(
                thread_id=thread.ident or 0,
                name=thread.name,
                started_at=time.time(),
                run_id=run_id,
                task_type=task_type,
                is_daemon=thread.daemon,
            )

    def unregister(self, thread_id: int) -> None:
        """Unregister a thread"""
        with self._lock:
            self._threads.pop(thread_id, None)

    def update_heartbeat(self, thread_id: int) -> None:
        """Update last seen time for a thread"""
        with self._lock:
            info = self._threads.get(thread_id)
            if info:
                info.last_seen_at = time.time()

    def get_active_threads(self) -> list[ThreadInfo]:
        """Get list of all tracked threads"""
        with self._lock:
            return list(self._threads.values())

    def get_stale_threads(self, threshold_seconds: int | None = None) -> list[ThreadInfo]:
        """Get threads that haven't updated in a while"""
        threshold = threshold_seconds or self._cleanup_threshold_seconds
        now = time.time()
        with self._lock:
            return [
                info for info in self._threads.values()
                if now - info.last_seen_at > threshold
            ]

    def cleanup_completed_threads(self) -> int:
        """Remove threads that are no longer alive"""
        active_thread_ids = {t.ident for t in threading.enumerate() if t.ident}
        removed_count = 0

        with self._lock:
            dead_thread_ids = [
                tid for tid in self._threads.keys()
                if tid not in active_thread_ids
            ]
            for tid in dead_thread_ids:
                self._threads.pop(tid, None)
                removed_count += 1

        return removed_count

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about tracked threads"""
        with self._lock:
            total = len(self._threads)
            by_type: dict[str, int] = {}
            daemon_count = 0

            for info in self._threads.values():
                by_type[info.task_type] = by_type.get(info.task_type, 0) + 1
                if info.is_daemon:
                    daemon_count += 1

            return {
                "total_tracked": total,
                "daemon_threads": daemon_count,
                "by_task_type": by_type,
                "stale_threads": len(self.get_stale_threads()),
            }


# Global thread tracker instance
_global_tracker: ThreadTracker | None = None
_tracker_lock = threading.Lock()


def get_thread_tracker(state_dir: Path | None = None) -> ThreadTracker:
    """Get or create the global thread tracker"""
    global _global_tracker
    with _tracker_lock:
        if _global_tracker is None:
            _global_tracker = ThreadTracker(state_dir)
        return _global_tracker


def start_tracked_thread(
    target: Any,
    run_id: str,
    task_type: str,
    name: str | None = None,
    daemon: bool = True,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> threading.Thread:
    """
    Start a thread with automatic tracking

    Args:
        target: Thread target function
        run_id: Unique run identifier
        task_type: Type of task (for statistics)
        name: Thread name
        daemon: Whether thread is daemon
        args: Positional arguments for target
        kwargs: Keyword arguments for target

    Returns:
        Started thread object
    """
    tracker = get_thread_tracker()

    def wrapped_target(*inner_args: Any, **inner_kwargs: Any) -> None:
        thread_id = threading.current_thread().ident or 0
        try:
            target(*inner_args, **inner_kwargs)
        finally:
            tracker.unregister(thread_id)

    thread = threading.Thread(
        target=wrapped_target,
        name=name or f"{task_type}-{run_id[:8]}",
        daemon=daemon,
        args=args,
        kwargs=kwargs or {},
    )
    thread.start()

    if thread.ident:
        tracker.register(thread, run_id, task_type)

    return thread


def cleanup_background_threads(force: bool = False) -> dict[str, int]:
    """
    Cleanup completed threads and optionally log stale ones

    Args:
        force: If True, also log warnings about stale threads

    Returns:
        Dictionary with cleanup statistics
    """
    tracker = get_thread_tracker()
    removed = tracker.cleanup_completed_threads()

    result = {
        "removed_completed": removed,
        "stale_detected": 0,
    }

    if force:
        stale = tracker.get_stale_threads()
        result["stale_detected"] = len(stale)

    return result
