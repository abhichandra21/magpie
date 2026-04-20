import csv
import io
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from magpie.webui.server import build_app

FIXTURES = Path(__file__).parent / "fixtures"


def _write_csv(path: Path, rows: list[dict]) -> None:
    cols = ["path", "status", "model", "caption", "keyword_count", "duration_ms", "error"]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in cols})


@pytest.fixture
def runs_dir(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def photo(tmp_path):
    # Copy a fixture JPEG that already has IPTC keywords (already_tagged)
    dst = tmp_path / "a.jpg"
    shutil.copyfile(FIXTURES / "already_tagged.jpg", dst)
    return dst


@pytest.fixture
def client(runs_dir):
    return TestClient(build_app(runs_dir=runs_dir))


def test_index_html_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "a cabinet of" in r.text
    assert "captioned" in r.text


def test_stats_empty(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "runs": 0,
        "tagged_total": 0,
        "tagged_week": 0,
        "last_models": [],
    }


def test_runs_and_stats_count(runs_dir, client, photo):
    _write_csv(
        runs_dir / "2026-04-20T10-00-00.csv",
        [
            {
                "path": str(photo),
                "status": "tagged",
                "model": "gemma4:26b",
                "caption": "a photo",
                "keyword_count": "3",
                "duration_ms": "1200",
                "error": "",
            }
        ],
    )
    _write_csv(
        runs_dir / "2026-04-20T11-00-00.csv",
        [
            {
                "path": str(photo),
                "status": "skipped",
                "model": "gemma4:26b",
                "caption": "",
                "keyword_count": "",
                "duration_ms": "10",
                "error": "",
            }
        ],
    )

    stats = client.get("/api/stats").json()
    assert stats["runs"] == 2
    assert stats["tagged_total"] == 1
    assert stats["last_models"] == ["gemma4:26b"]

    runs = client.get("/api/runs").json()
    # newest first
    assert runs[0]["id"] == "2026-04-20T11-00-00"
    assert runs[1]["tagged"] == 1


def test_run_detail_enriches_keywords_from_iptc(runs_dir, client, photo):
    _write_csv(
        runs_dir / "2026-04-20T12-00-00.csv",
        [
            {
                "path": str(photo),
                "status": "tagged",
                "model": "m",
                "caption": "seed caption",
                "keyword_count": "1",
                "duration_ms": "5",
                "error": "",
            }
        ],
    )
    body = client.get("/api/runs/2026-04-20T12-00-00").json()
    assert body["meta"]["tagged"] == 1
    row = body["rows"][0]
    assert "seed" in row["keywords"]


def test_run_detail_rejects_traversal(client):
    r = client.get("/api/runs/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404


def test_thumb_denies_unknown_path(client, tmp_path):
    outside = tmp_path / "notlogged.jpg"
    img = Image.new("RGB", (50, 50))
    img.save(outside)
    r = client.get("/api/thumb", params={"path": str(outside)})
    assert r.status_code == 403


def test_thumb_serves_listed_path(runs_dir, client, photo):
    _write_csv(
        runs_dir / "2026-04-20T13-00-00.csv",
        [
            {
                "path": str(photo),
                "status": "tagged",
                "model": "m",
                "caption": "c",
                "keyword_count": "1",
                "duration_ms": "5",
                "error": "",
            }
        ],
    )
    r = client.get("/api/thumb", params={"path": str(photo)})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    out = Image.open(io.BytesIO(r.content))
    assert out.format == "JPEG"
    assert max(out.size) <= 900


def test_thumb_404_when_file_missing(runs_dir, client, tmp_path):
    # CSV references a path that doesn't exist on disk
    ghost = tmp_path / "ghost.jpg"
    _write_csv(
        runs_dir / "2026-04-20T14-00-00.csv",
        [
            {
                "path": str(ghost),
                "status": "tagged",
                "model": "m",
                "caption": "",
                "keyword_count": "0",
                "duration_ms": "1",
                "error": "",
            }
        ],
    )
    r = client.get("/api/thumb", params={"path": str(ghost)})
    assert r.status_code == 404
