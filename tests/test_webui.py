import csv
import io
import shutil
from pathlib import Path
from types import SimpleNamespace

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
    assert "What would you like" in r.text
    assert 'id="run-form"' in r.text


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


def test_runs_parse_high_precision_timestamps(runs_dir, client, photo):
    _write_csv(
        runs_dir / "2026-04-20T10-00-00-123456.csv",
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
    runs = client.get("/api/runs").json()
    assert runs[0]["timestamp"] == "2026-04-20T10:00:00.123456"


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


def test_start_job_rejects_missing_path(client):
    r = client.post("/api/jobs", json={"path": "/does/not/exist/ever.jpg"})
    assert r.status_code == 400
    assert "not found" in r.json()["detail"]


def test_start_job_requires_path(client):
    r = client.post("/api/jobs", json={})
    assert r.status_code == 400


def test_validate_path_dir(client, tmp_path, photo):
    # tmp_path now contains photo (a.jpg)
    r = client.post("/api/validate", json={"path": str(tmp_path)})
    body = r.json()
    assert body["exists"] and body["kind"] == "dir"
    assert body["images"] >= 1


def test_validate_path_file(client, photo):
    r = client.post("/api/validate", json={"path": str(photo)})
    body = r.json()
    assert body["kind"] == "file"
    assert body["images"] == 1


def test_validate_path_missing(client):
    r = client.post("/api/validate", json={"path": "/no/such/place/zzz.jpg"})
    body = r.json()
    assert body["exists"] is False


def test_browse_under_root_lists_dirs(tmp_path, monkeypatch, runs_dir):
    monkeypatch.setenv("MAGPIE_BROWSE_ROOT", str(tmp_path))
    # rebuild app so the new env var is picked up
    import importlib

    import magpie.webui.server as srv
    importlib.reload(srv)
    fresh = TestClient(srv.build_app(runs_dir=runs_dir))

    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    shutil.copyfile(FIXTURES / "untagged.jpg", tmp_path / "snap.jpg")

    r = fresh.get("/api/browse")
    body = r.json()
    names = [d["name"] for d in body["dirs"]]
    files = [f["name"] for f in body["files"]]
    assert "alpha" in names and "beta" in names
    assert "snap.jpg" in files
    assert body["crumbs"][0]["path"] == str(tmp_path)


def test_browse_rejects_outside_root(tmp_path, monkeypatch, runs_dir):
    monkeypatch.setenv("MAGPIE_BROWSE_ROOT", str(tmp_path))
    import importlib

    import magpie.webui.server as srv
    importlib.reload(srv)
    fresh = TestClient(srv.build_app(runs_dir=runs_dir))
    r = fresh.get("/api/browse", params={"path": "/etc"})
    assert r.status_code == 403


def test_config_get_and_put_round_trip(tmp_path, monkeypatch, runs_dir):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg_path)
    fresh = TestClient(build_app(runs_dir=runs_dir))

    # initial GET creates a default config
    initial = fresh.get("/api/config").json()
    assert "mac" in [e["name"] for e in initial["endpoints"]]

    # PUT with edits
    put = fresh.put(
        "/api/config",
        json={
            "default_endpoint": "spark",
            "max_keywords": 17,
            "concurrency": 4,
            "endpoints": [
                {"name": "mac", "url": "http://localhost:11434/v1", "model": "gemma4:26b"},
                {"name": "spark", "url": "http://192.168.1.75:11434/v1", "model": "qwen3-vl:30b", "api_key": ""},
            ],
        },
    )
    assert put.status_code == 200, put.text
    after = put.json()
    assert after["default_endpoint"] == "spark"
    assert after["max_keywords"] == 17
    assert after["concurrency"] == 4

    # the prompt block must be preserved
    text = cfg_path.read_text()
    assert "[prompt]" in text
    assert "expert photo cataloger" in text


def test_config_put_accepts_non_identifier_library_names(tmp_path, monkeypatch, runs_dir):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg_path)
    fresh = TestClient(build_app(runs_dir=runs_dir))
    body = {
        "default_endpoint": "mac",
        "max_keywords": 25, "concurrency": 2,
        "endpoints": [{"name": "mac", "url": "http://x/v1", "model": "m"}],
        "libraries": [
            {"name": "family trip", "path": "/tmp"},
            {"name": "Chicago-Air-Show", "path": "/tmp"},
        ],
    }
    r = fresh.put("/api/config", json=body)
    assert r.status_code == 200, r.text
    text = cfg_path.read_text()
    assert '"family trip"' in text     # quoted (has space)
    assert "Chicago-Air-Show = " in text  # bare key (hyphens are TOML-legal)


def test_config_put_can_clear_existing_api_key(tmp_path, monkeypatch, runs_dir):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
default_endpoint = "mac"
max_keywords = 25
concurrency = 2

[endpoints.mac]
url = "http://x/v1"
model = "m"
api_key = "secret"

[prompt]
system = "s"
user_template = "u {hint}"
"""
    )
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg_path)
    fresh = TestClient(build_app(runs_dir=runs_dir))

    r = fresh.put(
        "/api/config",
        json={
            "default_endpoint": "mac",
            "max_keywords": 25,
            "concurrency": 2,
            "endpoints": [
                {"name": "mac", "url": "http://x/v1", "model": "m", "api_key": ""}
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert 'api_key = ""' in cfg_path.read_text()


def test_config_put_saves_libraries_then_endpoints_preserves_prompt(tmp_path, monkeypatch, runs_dir):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg_path)
    fresh = TestClient(build_app(runs_dir=runs_dir))
    # 1) first save introduces libraries
    fresh.put(
        "/api/config",
        json={
            "default_endpoint": "mac",
            "max_keywords": 25, "concurrency": 2,
            "endpoints": [{"name": "mac", "url": "http://x/v1", "model": "m"}],
            "libraries": [{"name": "pics", "path": "/tmp"}],
        },
    )
    # 2) second save omits libraries; should preserve the existing block
    #    without swallowing it into the prompt tail.
    fresh.put(
        "/api/config",
        json={
            "default_endpoint": "mac",
            "max_keywords": 30, "concurrency": 2,
            "endpoints": [{"name": "mac", "url": "http://x/v1", "model": "m2"}],
        },
    )
    text = cfg_path.read_text()
    # exactly one [libraries] block survives
    assert text.count("[libraries]") == 1
    assert text.count("[prompt]") == 1
    # max_keywords updated
    assert "max_keywords = 30" in text


def test_config_put_rejects_invalid(tmp_path, monkeypatch, runs_dir):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg_path)
    fresh = TestClient(build_app(runs_dir=runs_dir))
    r = fresh.put(
        "/api/config",
        json={
            "default_endpoint": "ghost",
            "max_keywords": 1, "concurrency": 1,
            "endpoints": [
                {"name": "mac", "url": "x", "model": "m"},
            ],
        },
    )
    assert r.status_code == 400
    assert "ghost" in r.json()["detail"]


def test_settings_html_served(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Settings" in r.text


def test_library_and_logs_pages_served(client):
    assert client.get("/library").status_code == 200
    assert "Library" in client.get("/library").text
    assert client.get("/logs").status_code == 200
    assert "Logs" in client.get("/logs").text


def test_logs_tail_returns_lines(client):
    # any prior request emits at least one uvicorn log line
    client.get("/api/stats")
    body = client.get("/api/logs?limit=20").json()
    assert "lines" in body
    assert isinstance(body["lines"], list)


def test_libraries_endpoint_lists_configured(tmp_path, monkeypatch, runs_dir):
    cfg = tmp_path / "config.toml"
    photos = tmp_path / "photos"
    photos.mkdir()
    shutil.copyfile(FIXTURES / "untagged.jpg", photos / "a.jpg")
    shutil.copyfile(FIXTURES / "already_tagged.jpg", photos / "b.jpg")
    cfg.write_text(
        f"""
default_endpoint = "mac"
max_keywords = 25
concurrency = 2

[endpoints.mac]
url = "http://x/v1"
model = "m"
api_key = ""

[libraries]
shots = "{photos}"

[prompt]
system = "s"
user_template = "u {{hint}}"
"""
    )
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg)
    fresh = TestClient(build_app(runs_dir=runs_dir))
    body = fresh.get("/api/libraries").json()
    libs = {lib["name"]: lib for lib in body["libraries"]}
    assert "shots" in libs
    assert libs["shots"]["count"] == 2

    detail = fresh.get("/api/library/shots").json()
    assert detail["meta"]["total"] == 2
    items_by_name = {it["name"]: it for it in detail["items"]}
    assert items_by_name["b.jpg"]["tagged"] is True
    assert items_by_name["a.jpg"]["tagged"] is False


def test_library_filter_tagged(tmp_path, monkeypatch, runs_dir):
    cfg = tmp_path / "config.toml"
    photos = tmp_path / "photos"
    photos.mkdir()
    shutil.copyfile(FIXTURES / "untagged.jpg", photos / "u.jpg")
    shutil.copyfile(FIXTURES / "already_tagged.jpg", photos / "t.jpg")
    cfg.write_text(
        f"""
default_endpoint = "mac"
max_keywords = 25
concurrency = 2
[endpoints.mac]
url = "http://x/v1"
model = "m"
api_key = ""
[libraries]
shots = "{photos}"
[prompt]
system = "s"
user_template = "u {{hint}}"
"""
    )
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg)
    fresh = TestClient(build_app(runs_dir=runs_dir))
    only_tagged = fresh.get("/api/library/shots?filter=tagged").json()
    assert [i["name"] for i in only_tagged["items"]] == ["t.jpg"]
    only_untagged = fresh.get("/api/library/shots?filter=untagged").json()
    assert [i["name"] for i in only_untagged["items"]] == ["u.jpg"]


def test_library_filter_applies_before_paging(tmp_path, monkeypatch, runs_dir):
    cfg = tmp_path / "config.toml"
    photos = tmp_path / "photos"
    photos.mkdir()
    for idx in range(60):
        shutil.copyfile(FIXTURES / "already_tagged.jpg", photos / f"t{idx:03d}.jpg")
    shutil.copyfile(FIXTURES / "untagged.jpg", photos / "z.jpg")
    cfg.write_text(
        f"""
default_endpoint = "mac"
max_keywords = 25
concurrency = 2
[endpoints.mac]
url = "http://x/v1"
model = "m"
api_key = ""
[libraries]
shots = "{photos}"
[prompt]
system = "s"
user_template = "u {{hint}}"
"""
    )
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg)
    fresh = TestClient(build_app(runs_dir=runs_dir))
    body = fresh.get(
        "/api/library/shots",
        params={"filter": "untagged", "offset": 0, "limit": 60},
    ).json()
    assert body["meta"]["total"] == 1
    assert [item["name"] for item in body["items"]] == ["z.jpg"]


def test_start_job_filter_untagged_submits_matching_files(tmp_path, monkeypatch, runs_dir):
    import magpie.webui.server as srv

    photos = tmp_path / "photos"
    photos.mkdir()
    tagged = photos / "t.jpg"
    untagged = photos / "u.jpg"
    shutil.copyfile(FIXTURES / "already_tagged.jpg", tagged)
    shutil.copyfile(FIXTURES / "untagged.jpg", untagged)

    captured: dict = {}

    def fake_submit(cfg, path, endpoint, hint, force, inputs=None):
        captured.update(
            {
                "cfg": cfg,
                "path": path,
                "endpoint": endpoint,
                "hint": hint,
                "force": force,
                "inputs": inputs,
            }
        )
        return SimpleNamespace(id="job123")

    monkeypatch.setattr(srv, "_load_config", lambda: object())
    monkeypatch.setattr(srv.MANAGER, "submit", fake_submit)
    fresh = TestClient(srv.build_app(runs_dir=runs_dir))

    r = fresh.post(
        "/api/jobs",
        json={"path": str(photos), "filter": "untagged", "force": False},
    )
    assert r.status_code == 202, r.text
    assert captured["path"] == str(photos)
    assert captured["force"] is True
    assert captured["inputs"] == [str(untagged)]


def test_start_job_filter_untagged_preserves_empty_selection(tmp_path, monkeypatch, runs_dir):
    import magpie.webui.server as srv

    photos = tmp_path / "photos"
    photos.mkdir()
    tagged = photos / "t.jpg"
    shutil.copyfile(FIXTURES / "already_tagged.jpg", tagged)

    captured: dict = {}

    def fake_submit(cfg, path, endpoint, hint, force, inputs=None):
        captured.update(
            {
                "cfg": cfg,
                "path": path,
                "endpoint": endpoint,
                "hint": hint,
                "force": force,
                "inputs": inputs,
            }
        )
        return SimpleNamespace(id="job123")

    monkeypatch.setattr(srv, "_load_config", lambda: object())
    monkeypatch.setattr(srv.MANAGER, "submit", fake_submit)
    fresh = TestClient(srv.build_app(runs_dir=runs_dir))

    r = fresh.post(
        "/api/jobs",
        json={"path": str(photos), "filter": "untagged", "force": False},
    )
    assert r.status_code == 202, r.text
    assert captured["path"] == str(photos)
    assert captured["force"] is True
    assert captured["inputs"] == []


def test_start_job_filtered_scan_failure_returns_503(tmp_path, monkeypatch, runs_dir):
    import magpie.webui.server as srv

    photos = tmp_path / "photos"
    photos.mkdir()
    shutil.copyfile(FIXTURES / "untagged.jpg", photos / "u.jpg")

    called = False

    def fake_submit(*_args, **_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(id="job123")

    monkeypatch.setattr(srv, "_load_config", lambda: object())
    monkeypatch.setattr(
        srv,
        "_read_tags",
        lambda *args, **kwargs: (_ for _ in ()).throw(srv.LibraryScanError("boom")),
    )
    monkeypatch.setattr(srv.MANAGER, "submit", fake_submit)
    fresh = TestClient(srv.build_app(runs_dir=runs_dir))

    r = fresh.post(
        "/api/jobs",
        json={"path": str(photos), "filter": "untagged", "force": False},
    )
    assert r.status_code == 503
    assert "could not scan metadata" in r.json()["detail"]
    assert called is False


def test_thumb_serves_library_path(tmp_path, monkeypatch, runs_dir):
    cfg = tmp_path / "config.toml"
    photos = tmp_path / "photos"
    photos.mkdir()
    shutil.copyfile(FIXTURES / "untagged.jpg", photos / "x.jpg")
    cfg.write_text(
        f"""
default_endpoint = "mac"
max_keywords = 25
concurrency = 2
[endpoints.mac]
url = "http://x/v1"
model = "m"
api_key = ""
[libraries]
shots = "{photos}"
[prompt]
system = "s"
user_template = "u {{hint}}"
"""
    )
    monkeypatch.setattr("magpie.webui.server.DEFAULT_CONFIG_PATH", cfg)
    monkeypatch.setattr("magpie.config.DEFAULT_CONFIG_PATH", cfg)
    fresh = TestClient(build_app(runs_dir=runs_dir))
    r = fresh.get("/api/thumb", params={"path": str(photos / "x.jpg")})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


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
