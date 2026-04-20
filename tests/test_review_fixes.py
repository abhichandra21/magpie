"""Regression tests for code-review P1/P2 fixes."""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import shutil
from pathlib import Path

import pytest
from PIL import Image
from watchdog.events import FileMovedEvent

from magpie.runner import BatchRunner
from magpie.tagger import _image_to_data_url
from magpie.watcher import Watcher, _EventBridge

FIXTURES = Path(__file__).parent / "fixtures"


# ---------- P1: HEIC decoding ----------

def test_image_to_data_url_decodes_heic(tmp_path):
    import pillow_heif

    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (600, 400), color=(70, 130, 180))
    buf = io.BytesIO()
    img.save(buf, format="HEIF", quality=85)
    heic_bytes = buf.getvalue()
    assert heic_bytes[:12].endswith(b"ftyp") or b"ftyp" in heic_bytes[:20]

    data_url = _image_to_data_url(heic_bytes)
    assert data_url.startswith("data:image/jpeg;base64,")
    jpeg = base64.b64decode(data_url.split(",", 1)[1])
    decoded = Image.open(io.BytesIO(jpeg))
    assert decoded.format == "JPEG"
    assert decoded.size == (600, 400)


# ---------- P2: EXIF orientation ----------

def test_image_to_data_url_applies_exif_orientation():
    # Build a 200×100 image and tag it with orientation=6 (rotate 90° CW)
    src = Image.new("RGB", (200, 100), color=(10, 20, 30))
    exif = src.getexif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    src.save(buf, format="JPEG", quality=90, exif=exif.tobytes())
    oriented_bytes = buf.getvalue()

    data_url = _image_to_data_url(oriented_bytes)
    jpeg = base64.b64decode(data_url.split(",", 1)[1])
    out = Image.open(io.BytesIO(jpeg))

    # After applying orientation=6, the logical image is now 100 wide × 200 tall.
    assert out.size == (100, 200)


# ---------- P1: on_moved uses dest_path ----------

def test_on_moved_enqueues_destination_path():
    loop = asyncio.new_event_loop()
    try:
        queue: asyncio.Queue[Path] = asyncio.Queue()
        bridge = _EventBridge(loop, queue)
        event = FileMovedEvent(src_path="/watch/IMG_1234.jpg.tmp",
                               dest_path="/watch/IMG_1234.jpg")
        bridge.on_moved(event)
        loop.run_until_complete(asyncio.sleep(0))
        assert queue.get_nowait() == Path("/watch/IMG_1234.jpg")
    finally:
        loop.close()


def test_on_moved_ignores_when_destination_not_image():
    loop = asyncio.new_event_loop()
    try:
        queue: asyncio.Queue[Path] = asyncio.Queue()
        bridge = _EventBridge(loop, queue)
        event = FileMovedEvent(src_path="/watch/IMG_1.jpg",
                               dest_path="/watch/IMG_1.txt")
        bridge.on_moved(event)
        loop.run_until_complete(asyncio.sleep(0))
        assert queue.empty()
    finally:
        loop.close()


# ---------- P2: CSV append across multiple runs ----------

@pytest.mark.asyncio
async def test_csv_append_preserves_rows_across_runs(tmp_path):
    csv_path = tmp_path / "run.csv"

    class StaticTagger:
        async def tag(self, image_bytes: bytes, hint: str = ""):
            from magpie.tagger import TagResult
            return TagResult(caption="c", keywords=["k"])

    class StaticWriter:
        def already_tagged(self, path: Path) -> bool:
            return False

        def write(self, path: Path, result, model_id: str) -> None:
            pass

    # Create two distinct images
    for n in ("a.jpg", "b.jpg"):
        shutil.copyfile(FIXTURES / "untagged.jpg", tmp_path / n)

    runner = BatchRunner(
        tagger=StaticTagger(),
        writer=StaticWriter(),
        model_id="m",
        concurrency=1,
        csv_path=csv_path,
    )
    await runner.run([tmp_path / "a.jpg"])
    await runner.run([tmp_path / "b.jpg"])

    rows = list(csv.DictReader(csv_path.open()))
    paths = {Path(r["path"]).name for r in rows}
    assert paths == {"a.jpg", "b.jpg"}


# ---------- P1: watcher retry on process failure uses CLI-style wrapper ----------

@pytest.mark.asyncio
async def test_watcher_retries_when_process_raises(tmp_path):
    """Simulate the cli.watch callback that raises when BatchRunner reports failed.
    Confirm Watcher._retry_process backs off and retries until success."""
    from watchdog.observers.polling import PollingObserver

    attempts = {"n": 0}
    succeeded = asyncio.Event()

    async def process(path: Path) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("tagging failed")
        succeeded.set()

    watcher = Watcher(
        paths=[tmp_path],
        process=process,
        observer_cls=PollingObserver,
        stable_seconds=0.1,
        poll_interval=0.05,
        backoff_seq=(0.05, 0.05, 0.05),
    )
    await watcher.start()
    try:
        await asyncio.sleep(0.15)
        shutil.copyfile(FIXTURES / "untagged.jpg", tmp_path / "photo.jpg")
        await asyncio.wait_for(succeeded.wait(), timeout=5.0)
    finally:
        await watcher.stop()

    assert attempts["n"] == 3
