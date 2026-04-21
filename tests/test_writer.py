import shutil
from pathlib import Path

import exiftool
import pytest

from magpie.tagger import TagResult
from magpie.writer import MetadataWriter, WriterError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def writer():
    w = MetadataWriter()
    yield w
    w.close()


@pytest.fixture
def untagged_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "untagged.jpg"
    shutil.copyfile(FIXTURES / "untagged.jpg", dst)
    return dst


@pytest.fixture
def tagged_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "already_tagged.jpg"
    shutil.copyfile(FIXTURES / "already_tagged.jpg", dst)
    return dst


def _read_tags(path: Path) -> dict:
    with exiftool.ExifToolHelper() as et:
        return et.get_metadata(str(path))[0]


def test_already_tagged_detects_magpie_marker(writer, tagged_copy):
    assert writer.already_tagged(tagged_copy) is True


def test_already_tagged_false_for_untagged(writer, untagged_copy):
    assert writer.already_tagged(untagged_copy) is False


def test_already_tagged_accepts_ai_tagger_prefix(writer, tmp_path):
    src = tmp_path / "legacy.jpg"
    shutil.copyfile(FIXTURES / "untagged.jpg", src)
    with exiftool.ExifToolHelper() as et:
        et.set_tags(
            str(src),
            tags={"XMP:CreatorTool": "ai-tagger/0.9"},
            params=["-overwrite_original"],
        )
    assert writer.already_tagged(src) is True


def test_write_sets_iptc_xmp_and_creator_tool(writer, untagged_copy):
    result = TagResult(caption="A dog on grass", keywords=["dog", "grass", "outdoors"])
    writer.write(untagged_copy, result, model_id="test-model")

    tags = _read_tags(untagged_copy)
    assert tags["IPTC:Caption-Abstract"] == "A dog on grass"
    kw = tags["IPTC:Keywords"]
    assert set(kw if isinstance(kw, list) else [kw]) == {"dog", "grass", "outdoors"}
    assert tags["XMP:Description"] == "A dog on grass"
    subj = tags["XMP:Subject"]
    assert set(subj if isinstance(subj, list) else [subj]) == {"dog", "grass", "outdoors"}
    assert tags["XMP:CreatorTool"] == "magpie/test-model"


def test_write_creates_original_backup_on_first_write(writer, untagged_copy):
    result = TagResult(caption="c", keywords=["k"])
    writer.write(untagged_copy, result, model_id="m")
    backup = untagged_copy.with_suffix(untagged_copy.suffix + "_original")
    assert backup.exists()


def test_write_then_already_tagged_true(writer, untagged_copy):
    result = TagResult(caption="c", keywords=["k"])
    writer.write(untagged_copy, result, model_id="m")
    assert writer.already_tagged(untagged_copy) is True


def test_write_preserves_file_mtime(writer, untagged_copy):
    import os
    import time

    # Back-date the test fixture so we can detect a rewrite touching mtime.
    past = time.time() - 7 * 24 * 3600  # one week ago
    os.utime(untagged_copy, (past, past))
    before = untagged_copy.stat().st_mtime

    writer.write(
        untagged_copy, TagResult(caption="c", keywords=["k"]), model_id="m"
    )
    after = untagged_copy.stat().st_mtime

    # allow a 1-second fudge for filesystem mtime resolution
    assert abs(after - before) < 1, f"mtime changed from {before} to {after}"


def test_write_raises_on_missing_file(writer, tmp_path):
    missing = tmp_path / "nope.jpg"
    with pytest.raises(WriterError):
        writer.write(missing, TagResult(caption="c", keywords=[]), model_id="m")
