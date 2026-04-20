"""FastAPI app for the magpie web UI.

Read-only dashboard over the run CSVs in ``~/.local/share/magpie/runs``.
Only paths that appear in at least one CSV are exposed via /api/thumb, so the
endpoint cannot be abused to read arbitrary files.
"""

from __future__ import annotations

import csv
import io
import socket
from datetime import datetime
from pathlib import Path

import exiftool
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

DEFAULT_RUNS_DIR = Path.home() / ".local" / "share" / "magpie" / "runs"
STATIC_DIR = Path(__file__).parent / "static"
THUMB_MAX_EDGE = 900


def _runs_dir() -> Path:
    return DEFAULT_RUNS_DIR


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _run_meta(path: Path) -> dict:
    rows = _read_csv(path)
    tagged = [r for r in rows if r.get("status") == "tagged"]
    skipped = sum(1 for r in rows if r.get("status") == "skipped")
    failed = sum(1 for r in rows if r.get("status") == "failed")
    models = sorted({r.get("model", "") for r in rows if r.get("model")})
    total_ms = sum(int(r.get("duration_ms") or 0) for r in rows)
    try:
        stamp = datetime.strptime(path.stem, "%Y-%m-%dT%H-%M-%S").isoformat()
    except ValueError:
        stamp = path.stem
    return {
        "id": path.stem,
        "timestamp": stamp,
        "tagged": len(tagged),
        "skipped": skipped,
        "failed": failed,
        "models": models,
        "total_ms": total_ms,
    }


def _read_tags(paths: list[str]) -> dict[str, dict]:
    """Pull IPTC keywords from files via exiftool. Skips missing files."""
    existing = [p for p in paths if p and Path(p).exists()]
    out: dict[str, dict] = {}
    if not existing:
        return out
    try:
        with exiftool.ExifToolHelper() as et:
            metas = et.get_tags(
                existing,
                tags=["IPTC:Keywords", "IPTC:Caption-Abstract"],
            )
    except Exception:
        return out
    for m in metas:
        src = m.get("SourceFile") or ""
        raw_kw = m.get("IPTC:Keywords") or []
        if isinstance(raw_kw, str):
            raw_kw = [raw_kw]
        out[src] = {
            "keywords": [str(k) for k in raw_kw],
            "caption": m.get("IPTC:Caption-Abstract") or "",
        }
    return out


def _all_known_paths(runs_dir: Path) -> set[str]:
    seen: set[str] = set()
    for csv_path in runs_dir.glob("*.csv"):
        for row in _read_csv(csv_path):
            p = row.get("path")
            if p:
                seen.add(p)
    return seen


def build_app(runs_dir: Path | None = None) -> FastAPI:
    runs_dir = runs_dir or _runs_dir()
    app = FastAPI(title="magpie webui", openapi_url=None, docs_url=None, redoc_url=None)

    # Path allow-list is re-derived on each request so newly tagged files are
    # reachable without a server restart. Cheap even for thousands of rows.

    @app.get("/api/stats")
    def stats() -> JSONResponse:
        runs = [_run_meta(p) for p in sorted(runs_dir.glob("*.csv"))]
        tagged_total = sum(r["tagged"] for r in runs)
        last_models: list[str] = runs[-1]["models"] if runs else []
        # "this week" = last 7 ISO days
        now = datetime.now()
        week: list[dict] = []
        for r in runs:
            try:
                ts = datetime.fromisoformat(r["timestamp"])
            except ValueError:
                continue
            if (now - ts).total_seconds() <= 7 * 24 * 3600:
                week.append(r)
        week_tagged = sum(r["tagged"] for r in week)
        return JSONResponse(
            {
                "runs": len(runs),
                "tagged_total": tagged_total,
                "tagged_week": week_tagged,
                "last_models": last_models,
            }
        )

    @app.get("/api/runs")
    def list_runs() -> JSONResponse:
        runs = [_run_meta(p) for p in sorted(runs_dir.glob("*.csv"), reverse=True)]
        return JSONResponse(runs)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> JSONResponse:
        if ".." in run_id or "/" in run_id:
            raise HTTPException(status_code=404, detail="run not found")
        path = runs_dir / f"{run_id}.csv"
        if not path.exists():
            raise HTTPException(status_code=404, detail="run not found")
        rows = _read_csv(path)
        meta = _run_meta(path)
        tagged_paths = [
            r["path"] for r in rows if r.get("status") == "tagged" and r.get("path")
        ]
        tags_by_path = _read_tags(tagged_paths)
        for row in rows:
            meta_tags = tags_by_path.get(row.get("path") or "", {})
            row["keywords"] = meta_tags.get("keywords") or []
        return JSONResponse({"meta": meta, "rows": rows})

    @app.get("/api/thumb")
    def thumbnail(path: str = Query(...)) -> Response:
        allowed = _all_known_paths(runs_dir)
        if path not in allowed:
            raise HTTPException(status_code=403, detail="path not in any run log")
        src = Path(path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="file missing on disk")
        try:
            with Image.open(src) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=82, optimize=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"decode: {exc}") from exc
        return Response(
            content=buf.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        html = STATIC_DIR / "index.html"
        if not html.exists():
            raise HTTPException(status_code=500, detail="index.html missing")
        return FileResponse(html, media_type="text/html")

    return app


def _pick_port(requested: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", requested))
            return requested
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def serve(host: str = "127.0.0.1", port: int = 7799, open_browser: bool = True) -> None:
    import uvicorn

    port = _pick_port(port)
    app = build_app()
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.7, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


