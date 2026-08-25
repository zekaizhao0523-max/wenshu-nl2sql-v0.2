"""后台任务管理（内存）。"""

from __future__ import annotations

import inspect
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass
class Job:
    job_id: str
    name: str
    status: str = "pending"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    progress_pct: float = 0.0
    progress_message: str = ""
    progress_done: int = 0
    progress_total: int = 0


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, name: str, fn: Callable[..., dict]) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id=job_id, name=name)
        with self._lock:
            self._jobs[job_id] = job

        def set_progress(
            *,
            pct: float | None = None,
            message: str | None = None,
            done: int | None = None,
            total: int | None = None,
        ) -> None:
            with self._lock:
                j = self._jobs.get(job_id)
                if not j:
                    return
                if pct is not None:
                    j.progress_pct = max(0.0, min(100.0, float(pct)))
                if message is not None:
                    j.progress_message = message
                if done is not None:
                    j.progress_done = int(done)
                if total is not None:
                    j.progress_total = int(total)

        def runner() -> None:
            job.status = "running"
            job.started_at = datetime.now().isoformat(timespec="seconds")
            try:
                sig = inspect.signature(fn)
                if "set_progress" in sig.parameters:
                    job.result = fn(set_progress=set_progress)
                else:
                    job.result = fn()
                job.status = "success"
                if job.progress_pct < 100:
                    job.progress_pct = 100.0
                    if not job.progress_message:
                        job.progress_message = "完成"
            except Exception as exc:
                job.status = "failed"
                job.error = f"{exc}\n{traceback.format_exc()}"
            finally:
                job.finished_at = datetime.now().isoformat(timespec="seconds")

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]


job_manager = JobManager()
