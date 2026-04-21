"""In-process job runner for the web UI.

A single global registry tracks running/completed magpie tag jobs so the UI
can drive them and stream progress. Jobs run on a background asyncio task
in a dedicated thread-owned loop so FastAPI's sync endpoints can interact
with them safely.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from magpie.config import Config
from magpie.runner import BatchRunner, default_csv_path
from magpie.tagger import Tagger, TagResult
from magpie.writer import MetadataWriter


@dataclass
class JobEvent:
    kind: str  # "started" | "file" | "done" | "error"
    data: dict
    ts: float = field(default_factory=time.time)


@dataclass
class Job:
    id: str
    path: str
    endpoint: str
    model: str
    hint: str
    force: bool
    inputs: tuple[str, ...] = field(default_factory=tuple, repr=False)
    status: str = "queued"  # queued | running | done | failed
    started: float | None = None
    finished: float | None = None
    total: int = 0
    tagged: int = 0
    skipped: int = 0
    failed: int = 0
    current: str | None = None
    error: str | None = None
    csv_path: str | None = None
    events: deque[JobEvent] = field(default_factory=lambda: deque(maxlen=500))
    _cond: asyncio.Condition | None = None


class JobManager:
    """Thread-backed event loop hosting magpie tag jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop and self._loop.is_running():
            return self._loop
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="magpie-webui-jobs", daemon=True
        )
        self._thread.start()
        return self._loop

    def submit(
        self,
        cfg: Config,
        path: str,
        endpoint: str | None,
        hint: str,
        force: bool,
        inputs: list[str] | None = None,
    ) -> Job:
        ep = cfg.endpoint(endpoint)
        job = Job(
            id=uuid.uuid4().hex[:12],
            path=path,
            endpoint=endpoint or cfg.default_endpoint,
            model=ep.model,
            hint=hint,
            force=force,
            inputs=tuple([path] if inputs is None else inputs),
        )
        with self._lock:
            self._jobs[job.id] = job
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(self._run(job, cfg, ep), loop)
        # bubble unexpected exceptions to the job record instead of silently dying
        fut.add_done_callback(lambda f: self._finalize(job, f))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.started or 0, reverse=True)

    def _finalize(self, job: Job, fut) -> None:
        try:
            fut.result()
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished = time.time()
            job.events.append(JobEvent("error", {"error": job.error}))

    async def _run(self, job: Job, cfg: Config, ep) -> None:
        job.status = "running"
        job.started = time.time()
        csv_path = default_csv_path()
        job.csv_path = str(csv_path)
        job.events.append(
            JobEvent(
                "started",
                {"path": job.path, "model": job.model, "endpoint": job.endpoint},
            )
        )

        tagger = Tagger(endpoint=ep, prompt=cfg.prompt, max_keywords=cfg.max_keywords)

        class _ProgressHook:
            def __init__(self, job: Job) -> None:
                self._job = job

            def total(self, n: int) -> None:
                self._job.total = n
                self._job.events.append(JobEvent("total", {"total": n}))

            def advance(self) -> None:
                pass  # BatchRunner never calls advance with file context — see wrapped tagger

        progress = _ProgressHook(job)

        class _TaggerAdapter:
            def __init__(self, tagger: Tagger, job: Job) -> None:
                self._tagger = tagger
                self._job = job

            async def tag(self, image_bytes: bytes, hint: str = "") -> TagResult:
                try:
                    return await self._tagger.tag(image_bytes, hint=hint)
                except Exception as exc:
                    self._job.failed += 1
                    self._job.events.append(
                        JobEvent(
                            "file",
                            {
                                "path": self._job.current or "",
                                "status": "failed",
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    )
                    raise

        adapted = _TaggerAdapter(tagger, job)

        class _WriterProxy:
            def __init__(self, inner: MetadataWriter, job: Job) -> None:
                self._inner = inner
                self._job = job

            def already_tagged(self, path: Path) -> bool:
                self._job.current = str(path)
                is_tagged = self._inner.already_tagged(path)
                if is_tagged:
                    self._job.skipped += 1
                    self._job.events.append(
                        JobEvent(
                            "file",
                            {"path": str(path), "status": "skipped"},
                        )
                    )
                return is_tagged

            def write(self, path: Path, result: TagResult, model_id: str) -> None:
                try:
                    self._inner.write(path, result, model_id)
                except Exception as exc:
                    self._job.failed += 1
                    self._job.events.append(
                        JobEvent(
                            "file",
                            {
                                "path": str(path),
                                "status": "failed",
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    )
                    raise
                self._job.tagged += 1
                self._job.events.append(
                    JobEvent(
                        "file",
                        {
                            "path": str(path),
                            "status": "tagged",
                            "caption": result.caption,
                            "keyword_count": len(result.keywords),
                        },
                    )
                )

        with MetadataWriter() as raw_writer:
            writer = _WriterProxy(raw_writer, job)
            runner = BatchRunner(
                tagger=adapted,
                writer=writer,
                model_id=ep.model,
                concurrency=cfg.concurrency,
                csv_path=csv_path,
                hint=job.hint,
                progress=progress,
            )
            summary = await runner.run(
                [Path(raw_path) for raw_path in job.inputs],
                force=job.force,
            )

        job.tagged = summary.tagged
        job.skipped = summary.skipped
        job.failed = summary.failed
        job.finished = time.time()
        job.status = "failed" if summary.failed and summary.tagged == 0 else "done"
        job.events.append(
            JobEvent(
                "done",
                {
                    "tagged": summary.tagged,
                    "skipped": summary.skipped,
                    "failed": summary.failed,
                    "csv": str(csv_path),
                },
            )
        )


MANAGER = JobManager()
