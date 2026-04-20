"""Watcher: observes folders and feeds new images to a process coroutine."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Protocol

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from magpie.runner import IMAGE_EXTS

DEFAULT_STABLE_SECONDS = 2.0
DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_BACKOFF_SEQ: tuple[float, ...] = (1, 2, 4, 8, 16, 32, 60)

Processor = Callable[[Path], Awaitable[None]]


class _ObserverLike(Protocol):
    def schedule(self, handler, path: str, recursive: bool = False): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...


class _EventBridge(FileSystemEventHandler):
    def __init__(
        self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Path]
    ) -> None:
        self._loop = loop
        self._queue = queue

    def _maybe_enqueue(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in IMAGE_EXTS:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, path)

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)


class Watcher:
    def __init__(
        self,
        paths: Iterable[Path],
        process: Processor,
        observer_cls: type[_ObserverLike] | None = None,
        stable_seconds: float = DEFAULT_STABLE_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        backoff_seq: tuple[float, ...] = DEFAULT_BACKOFF_SEQ,
    ) -> None:
        self._paths = [Path(p) for p in paths]
        self._process = process
        self._observer_cls = observer_cls or Observer
        self._stable_seconds = stable_seconds
        self._poll_interval = poll_interval
        self._backoff_seq = backoff_seq

        self._queue: asyncio.Queue[Path] | None = None
        self._observer: _ObserverLike | None = None
        self._worker: asyncio.Task | None = None
        self._stopped = False
        self._pending: dict[Path, asyncio.Task] = {}

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._observer = self._observer_cls()
        handler = _EventBridge(loop, self._queue)
        for p in self._paths:
            p.mkdir(parents=True, exist_ok=True)
            self._observer.schedule(handler, str(p), recursive=True)
        self._observer.start()
        self._worker = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._observer is not None:
            self._observer.stop()
            await asyncio.to_thread(self._observer.join, 5.0)
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._worker
        for task in list(self._pending.values()):
            task.cancel()
        self._pending.clear()

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            path = await self._queue.get()
            if path in self._pending and not self._pending[path].done():
                continue
            self._pending[path] = asyncio.create_task(self._handle(path))

    async def _handle(self, path: Path) -> None:
        try:
            await self._wait_stable(path)
            if not path.exists():  # noqa: ASYNC240
                return
            await self._retry_process(path)
        finally:
            self._pending.pop(path, None)

    async def _wait_stable(self, path: Path) -> None:
        last_size = -1
        last_change = asyncio.get_running_loop().time()
        loop = asyncio.get_running_loop()
        while True:
            try:
                size = path.stat().st_size  # noqa: ASYNC240
            except FileNotFoundError:
                await asyncio.sleep(self._poll_interval)
                continue
            now = loop.time()
            if size != last_size:
                last_size = size
                last_change = now
            elif now - last_change >= self._stable_seconds:
                return
            await asyncio.sleep(self._poll_interval)

    async def _retry_process(self, path: Path) -> None:
        delays = list(self._backoff_seq)
        attempt = 0
        while True:
            try:
                await self._process(path)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                delay = delays[min(attempt, len(delays) - 1)]
                print(
                    f"magpie.watcher: process failed for {path} "
                    f"(attempt {attempt + 1}): {exc!r}; retrying in {delay}s",
                    file=sys.stderr,
                )
                attempt += 1
                await asyncio.sleep(delay)
