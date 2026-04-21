import asyncio
import csv
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from magpie.runner import BatchRunner, RunStatus, default_csv_path
from magpie.tagger import TagResult

FIXTURES = Path(__file__).parent / "fixtures"


class FakeTagger:
    def __init__(self, result: TagResult | None = None, raises: Exception | None = None):
        self._result = result or TagResult(caption="c", keywords=["k1", "k2"])
        self._raises = raises
        self.calls: list[tuple[int, str]] = []

    async def tag(self, image_bytes: bytes, hint: str = "") -> TagResult:
        self.calls.append((len(image_bytes), hint))
        await asyncio.sleep(0)
        if self._raises:
            raise self._raises
        return self._result


class FakeWriter:
    def __init__(self, tagged: set[Path] | None = None):
        self._tagged = set(tagged or set())
        self.writes: list[tuple[Path, TagResult, str]] = []

    def already_tagged(self, path: Path) -> bool:
        return path in self._tagged

    def write(self, path: Path, result: TagResult, model_id: str) -> None:
        self.writes.append((path, result, model_id))
        self._tagged.add(path)

    def close(self) -> None:
        pass


def _copy(name: str, dst: Path) -> Path:
    shutil.copyfile(FIXTURES / name, dst)
    return dst


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    _copy("untagged.jpg", tmp_path / "a.jpg")
    _copy("untagged.jpg", tmp_path / "b.JPEG")
    _copy("already_tagged.jpg", tmp_path / "c.jpg")
    sub = tmp_path / "sub"
    sub.mkdir()
    _copy("untagged.jpg", sub / "d.jpg")
    (tmp_path / "ignore.txt").write_text("not an image")
    return tmp_path


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    return tmp_path / "run.csv"


@pytest.mark.asyncio
async def test_walks_recursive_and_filters_extensions(sample_dir, csv_path):
    tagger = FakeTagger()
    writer = FakeWriter(tagged={sample_dir / "c.jpg"})
    runner = BatchRunner(
        tagger=tagger, writer=writer, model_id="m1", concurrency=2, csv_path=csv_path
    )
    summary = await runner.run([sample_dir])
    # 3 non-tagged jpegs processed, 1 skipped, txt ignored
    written_paths = {p for p, _, _ in writer.writes}
    assert written_paths == {
        sample_dir / "a.jpg",
        sample_dir / "b.JPEG",
        sample_dir / "sub" / "d.jpg",
    }
    assert summary.tagged == 3
    assert summary.skipped == 1
    assert summary.failed == 0


@pytest.mark.asyncio
async def test_skipped_when_already_tagged(sample_dir, csv_path):
    tagger = FakeTagger()
    writer = FakeWriter(tagged={sample_dir / "c.jpg"})
    runner = BatchRunner(
        tagger=tagger, writer=writer, model_id="m", concurrency=2, csv_path=csv_path
    )
    await runner.run([sample_dir])
    # writer.write never called for c.jpg
    assert all(p.name != "c.jpg" for p, _, _ in writer.writes)


@pytest.mark.asyncio
async def test_force_bypasses_already_tagged(sample_dir, csv_path):
    tagger = FakeTagger()
    writer = FakeWriter(tagged={sample_dir / "c.jpg"})
    runner = BatchRunner(
        tagger=tagger, writer=writer, model_id="m", concurrency=2, csv_path=csv_path
    )
    summary = await runner.run([sample_dir], force=True)
    assert summary.tagged == 4
    assert summary.skipped == 0


@pytest.mark.asyncio
async def test_csv_has_expected_columns_and_rows(sample_dir, csv_path):
    tagger = FakeTagger(TagResult(caption="hello", keywords=["x", "y", "z"]))
    writer = FakeWriter(tagged={sample_dir / "c.jpg"})
    runner = BatchRunner(
        tagger=tagger, writer=writer, model_id="vmodel", concurrency=2, csv_path=csv_path
    )
    await runner.run([sample_dir])

    rows = list(csv.DictReader(csv_path.open()))
    assert {r["status"] for r in rows} == {"tagged", "skipped"}
    cols = set(rows[0].keys())
    assert cols == {
        "path",
        "status",
        "model",
        "caption",
        "keyword_count",
        "duration_ms",
        "error",
    }
    tagged_rows = [r for r in rows if r["status"] == "tagged"]
    assert all(r["model"] == "vmodel" for r in tagged_rows)
    assert all(r["caption"] == "hello" for r in tagged_rows)
    assert all(r["keyword_count"] == "3" for r in tagged_rows)


@pytest.mark.asyncio
async def test_tagger_failure_logged_and_continues(sample_dir, csv_path):
    tagger = FakeTagger(raises=RuntimeError("boom"))
    writer = FakeWriter(tagged=set())
    runner = BatchRunner(
        tagger=tagger, writer=writer, model_id="m", concurrency=2, csv_path=csv_path
    )
    summary = await runner.run([sample_dir])
    assert summary.failed >= 1
    assert summary.tagged == 0

    rows = list(csv.DictReader(csv_path.open()))
    fail_rows = [r for r in rows if r["status"] == "failed"]
    assert fail_rows
    assert any("boom" in r["error"] for r in fail_rows)


@pytest.mark.asyncio
async def test_status_enum_values():
    assert RunStatus.TAGGED.value == "tagged"
    assert RunStatus.SKIPPED.value == "skipped"
    assert RunStatus.FAILED.value == "failed"


@pytest.mark.asyncio
async def test_respects_concurrency_semaphore(sample_dir, csv_path):
    # Confirm at most N in-flight by instrumenting the tagger.
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    class CountingTagger:
        async def tag(self, image_bytes: bytes, hint: str = "") -> TagResult:
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return TagResult(caption="c", keywords=[])

    writer = FakeWriter()
    runner = BatchRunner(
        tagger=CountingTagger(),
        writer=writer,
        model_id="m",
        concurrency=2,
        csv_path=csv_path,
    )
    await runner.run([sample_dir])
    assert peak <= 2


@pytest.mark.asyncio
async def test_single_file_path_is_processed(tmp_path, csv_path):
    f = tmp_path / "one.jpg"
    _copy("untagged.jpg", f)
    tagger = FakeTagger()
    writer = FakeWriter()
    runner = BatchRunner(
        tagger=tagger, writer=writer, model_id="m", concurrency=2, csv_path=csv_path
    )
    summary = await runner.run([f])
    assert summary.tagged == 1


def test_default_csv_path_uses_subsecond_precision():
    early = default_csv_path(datetime(2026, 4, 20, 10, 0, 0, 123456))
    later = default_csv_path(datetime(2026, 4, 20, 10, 0, 0, 123457))
    assert early != later
    assert early.name.endswith("2026-04-20T10-00-00-123456.csv")
