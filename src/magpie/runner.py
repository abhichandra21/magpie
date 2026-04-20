"""BatchRunner: walk paths, tag, write, log CSV. Concurrency-bounded."""

from __future__ import annotations

import asyncio
import csv
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from magpie.tagger import TagResult

IMAGE_EXTS = {".jpg", ".jpeg", ".heic", ".heif"}


class RunStatus(StrEnum):
    TAGGED = "tagged"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class RunSummary:
    tagged: int = 0
    skipped: int = 0
    failed: int = 0

    def total(self) -> int:
        return self.tagged + self.skipped + self.failed


class _TaggerProto(Protocol):
    async def tag(self, image_bytes: bytes, hint: str = "") -> TagResult: ...


class _WriterProto(Protocol):
    def already_tagged(self, path: Path) -> bool: ...
    def write(self, path: Path, result: TagResult, model_id: str) -> None: ...


@dataclass
class _Row:
    path: Path
    status: RunStatus
    model: str
    caption: str = ""
    keyword_count: int = 0
    duration_ms: int = 0
    error: str = ""


class _ProgressLike(Protocol):
    def total(self, n: int) -> None: ...
    def advance(self) -> None: ...


class BatchRunner:
    CSV_FIELDS = (
        "path",
        "status",
        "model",
        "caption",
        "keyword_count",
        "duration_ms",
        "error",
    )

    def __init__(
        self,
        tagger: _TaggerProto,
        writer: _WriterProto,
        model_id: str,
        concurrency: int = 2,
        csv_path: Path | None = None,
        hint: str = "",
        progress: _ProgressLike | None = None,
    ) -> None:
        self._tagger = tagger
        self._writer = writer
        self._model_id = model_id
        self._concurrency = max(1, concurrency)
        self._csv_path = csv_path or default_csv_path()
        self._hint = hint
        self._progress = progress

    async def run(
        self, paths: Iterable[Path], force: bool = False
    ) -> RunSummary:
        files = list(_iter_images(paths))
        summary = RunSummary()
        sem = asyncio.Semaphore(self._concurrency)
        rows: list[_Row] = []
        rows_lock = asyncio.Lock()

        self._csv_path.parent.mkdir(parents=True, exist_ok=True)

        async def process(path: Path) -> None:
            async with sem:
                row = await self._process_one(path, force=force)
            async with rows_lock:
                rows.append(row)
                if row.status is RunStatus.TAGGED:
                    summary.tagged += 1
                elif row.status is RunStatus.SKIPPED:
                    summary.skipped += 1
                else:
                    summary.failed += 1
                if self._progress is not None:
                    self._progress.advance()

        if self._progress is not None:
            self._progress.total(len(files))

        try:
            await asyncio.gather(*(process(p) for p in files))
        finally:
            _write_csv(self._csv_path, rows)

        return summary

    async def _process_one(self, path: Path, force: bool) -> _Row:
        start = time.monotonic()
        try:
            if not force and self._writer.already_tagged(path):
                return _Row(
                    path=path,
                    status=RunStatus.SKIPPED,
                    model=self._model_id,
                    duration_ms=_ms(start),
                )
            image_bytes = await asyncio.to_thread(path.read_bytes)
            result = await self._tagger.tag(image_bytes, hint=self._hint)
            self._writer.write(path, result, model_id=self._model_id)
            return _Row(
                path=path,
                status=RunStatus.TAGGED,
                model=self._model_id,
                caption=result.caption,
                keyword_count=len(result.keywords),
                duration_ms=_ms(start),
            )
        except Exception as exc:
            return _Row(
                path=path,
                status=RunStatus.FAILED,
                model=self._model_id,
                duration_ms=_ms(start),
                error=f"{type(exc).__name__}: {exc}",
            )


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _iter_images(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_file():
            if p.suffix.lower() in IMAGE_EXTS:
                out.append(p)
            continue
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in IMAGE_EXTS:
                    out.append(child)
    return out


def _write_csv(path: Path, rows: list[_Row]) -> None:
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=BatchRunner.CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "path": str(r.path),
                    "status": r.status.value,
                    "model": r.model,
                    "caption": r.caption,
                    "keyword_count": r.keyword_count,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                }
            )


def default_csv_path(now: datetime | None = None) -> Path:
    now = now or datetime.now()
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S")
    return Path.home() / ".local" / "share" / "magpie" / "runs" / f"{stamp}.csv"
