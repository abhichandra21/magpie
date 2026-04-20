import asyncio
import shutil
from pathlib import Path

import pytest
from watchdog.observers.polling import PollingObserver

from magpie.watcher import Watcher

FIXTURES = Path(__file__).parent / "fixtures"


class Recorder:
    def __init__(self, raises_times: int = 0):
        self.seen: list[Path] = []
        self._raises_remaining = raises_times
        self.call_count = 0
        self.event = asyncio.Event()

    async def __call__(self, path: Path) -> None:
        self.call_count += 1
        if self._raises_remaining > 0:
            self._raises_remaining -= 1
            raise RuntimeError("endpoint down")
        self.seen.append(path)
        self.event.set()


def _drop(name: str, dst: Path) -> Path:
    shutil.copyfile(FIXTURES / name, dst)
    return dst


@pytest.mark.asyncio
async def test_enqueues_new_jpeg(tmp_path):
    rec = Recorder()
    watcher = Watcher(
        paths=[tmp_path],
        process=rec,
        observer_cls=PollingObserver,
        stable_seconds=0.1,
        poll_interval=0.05,
        backoff_seq=(0.05,),
    )
    await watcher.start()
    try:
        await asyncio.sleep(0.15)
        _drop("untagged.jpg", tmp_path / "new.jpg")
        await asyncio.wait_for(rec.event.wait(), timeout=5.0)
    finally:
        await watcher.stop()

    assert (tmp_path / "new.jpg") in rec.seen


@pytest.mark.asyncio
async def test_ignores_non_image_extensions(tmp_path):
    rec = Recorder()
    watcher = Watcher(
        paths=[tmp_path],
        process=rec,
        observer_cls=PollingObserver,
        stable_seconds=0.1,
        poll_interval=0.05,
    )
    await watcher.start()
    try:
        await asyncio.sleep(0.15)
        (tmp_path / "readme.txt").write_text("hi")
        _drop("untagged.jpg", tmp_path / "ok.jpg")
        await asyncio.wait_for(rec.event.wait(), timeout=5.0)
    finally:
        await watcher.stop()

    assert all(p.suffix.lower() != ".txt" for p in rec.seen)
    assert (tmp_path / "ok.jpg") in rec.seen


@pytest.mark.asyncio
async def test_waits_for_stable_size(tmp_path):
    rec = Recorder()
    watcher = Watcher(
        paths=[tmp_path],
        process=rec,
        observer_cls=PollingObserver,
        stable_seconds=0.3,
        poll_interval=0.05,
    )
    await watcher.start()
    try:
        target = tmp_path / "slow.jpg"
        await asyncio.sleep(0.15)
        # simulate a growing write
        with target.open("wb") as fh:
            fh.write(b"\xff\xd8\xff")
            await asyncio.sleep(0.1)
            fh.write(b"x" * 512)
            await asyncio.sleep(0.1)
            fh.write(b"y" * 512)
        # at this point file is closed and size-stable; finish via real jpeg
        _drop("untagged.jpg", target)
        await asyncio.wait_for(rec.event.wait(), timeout=5.0)
    finally:
        await watcher.stop()
    # should have been processed exactly once despite multiple modifications
    assert rec.call_count == 1


@pytest.mark.asyncio
async def test_retries_with_backoff_on_process_error(tmp_path):
    rec = Recorder(raises_times=2)
    watcher = Watcher(
        paths=[tmp_path],
        process=rec,
        observer_cls=PollingObserver,
        stable_seconds=0.1,
        poll_interval=0.05,
        backoff_seq=(0.05, 0.05, 0.05),
    )
    await watcher.start()
    try:
        await asyncio.sleep(0.15)
        _drop("untagged.jpg", tmp_path / "retry.jpg")
        await asyncio.wait_for(rec.event.wait(), timeout=5.0)
    finally:
        await watcher.stop()

    # 2 failures + 1 success = 3 calls
    assert rec.call_count == 3
    assert (tmp_path / "retry.jpg") in rec.seen


@pytest.mark.asyncio
async def test_stop_is_idempotent(tmp_path):
    rec = Recorder()
    watcher = Watcher(
        paths=[tmp_path],
        process=rec,
        observer_cls=PollingObserver,
        stable_seconds=0.1,
        poll_interval=0.05,
    )
    await watcher.start()
    await watcher.stop()
    await watcher.stop()
