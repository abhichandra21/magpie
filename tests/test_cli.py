"""End-to-end CLI integration: real exiftool, mocked endpoint."""

import json
import shutil
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from magpie.cli import app

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _chat_response(content: str) -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def _write_cfg(path: Path) -> Path:
    path.write_text(
        """
default_endpoint = "local"
max_keywords = 10
concurrency = 2

[endpoints.local]
url = "http://endpoint.test/v1"
model = "test-model"
api_key = ""

[prompt]
system = "reply strict JSON"
user_template = "Analyze. Hint: {hint}"
"""
    )
    return path


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("tag", "watch", "config"):
        assert cmd in result.stdout


@respx.mock
def test_tag_end_to_end(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # isolate default_csv_path
    cfg_path = _write_cfg(tmp_path / "cfg.toml")

    photos = tmp_path / "photos"
    photos.mkdir()
    target = photos / "a.jpg"
    shutil.copyfile(FIXTURES / "untagged.jpg", target)

    payload = {"caption": "a scene", "keywords": ["one", "two"]}
    respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )

    result = runner.invoke(
        app, ["tag", str(photos), "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.stdout
    assert "tagged=1" in result.stdout
    assert "skipped=0" in result.stdout

    # Verify metadata landed in the file
    import exiftool

    with exiftool.ExifToolHelper() as et:
        meta = et.get_metadata(str(target))[0]
    assert meta["IPTC:Caption-Abstract"] == "a scene"
    assert meta["XMP:CreatorTool"] == "magpie/test-model"


@respx.mock
def test_tag_reruns_as_noop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _write_cfg(tmp_path / "cfg.toml")

    photos = tmp_path / "photos"
    photos.mkdir()
    target = photos / "a.jpg"
    shutil.copyfile(FIXTURES / "untagged.jpg", target)

    payload = {"caption": "scene", "keywords": ["k"]}
    respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )

    r1 = runner.invoke(app, ["tag", str(photos), "--config", str(cfg_path)])
    assert r1.exit_code == 0 and "tagged=1" in r1.stdout

    r2 = runner.invoke(app, ["tag", str(photos), "--config", str(cfg_path)])
    assert r2.exit_code == 0
    assert "tagged=0" in r2.stdout
    assert "skipped=1" in r2.stdout


@respx.mock
def test_tag_respects_force(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _write_cfg(tmp_path / "cfg.toml")

    photos = tmp_path / "photos"
    photos.mkdir()
    target = photos / "a.jpg"
    shutil.copyfile(FIXTURES / "already_tagged.jpg", target)

    payload = {"caption": "forced", "keywords": ["k"]}
    respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )

    r = runner.invoke(
        app, ["tag", str(photos), "--config", str(cfg_path), "--force"]
    )
    assert r.exit_code == 0
    assert "tagged=1" in r.stdout
