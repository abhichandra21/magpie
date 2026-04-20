"""FastAPI app for the magpie web UI.

Read-only dashboard over the run CSVs in ``~/.local/share/magpie/runs``.
Only paths that appear in at least one CSV are exposed via /api/thumb, so the
endpoint cannot be abused to read arbitrary files.
"""

from __future__ import annotations

import csv
import io
import os
import socket
import tomllib
from datetime import datetime
from pathlib import Path

import exiftool
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from magpie.config import DEFAULT_CONFIG_PATH, Config, ConfigError
from magpie.runner import IMAGE_EXTS
from magpie.webui.jobs import MANAGER

BROWSE_ROOT = Path(os.environ.get("MAGPIE_BROWSE_ROOT") or Path.home()).resolve()

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


def _load_config() -> Config:
    try:
        return Config.load()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=f"config: {exc}") from exc


def _job_to_dict(job) -> dict:
    return {
        "id": job.id,
        "path": job.path,
        "endpoint": job.endpoint,
        "model": job.model,
        "hint": job.hint,
        "force": job.force,
        "status": job.status,
        "started": job.started,
        "finished": job.finished,
        "total": job.total,
        "tagged": job.tagged,
        "skipped": job.skipped,
        "failed": job.failed,
        "current": job.current,
        "error": job.error,
        "csv_path": job.csv_path,
        "events": [
            {"kind": e.kind, "ts": e.ts, "data": e.data} for e in list(job.events)
        ],
    }


def build_app(runs_dir: Path | None = None) -> FastAPI:
    runs_dir = runs_dir or _runs_dir()
    app = FastAPI(title="magpie webui", openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/api/endpoints")
    def endpoints() -> JSONResponse:
        cfg = _load_config()
        return JSONResponse(
            {
                "default": cfg.default_endpoint,
                "endpoints": [
                    {"name": n, "model": e.model, "url": e.url}
                    for n, e in sorted(cfg.endpoints.items())
                ],
            }
        )

    @app.post("/api/jobs")
    def start_job(payload: dict = Body(default_factory=dict)) -> JSONResponse:  # noqa: B008
        path = (payload or {}).get("path", "").strip()
        endpoint = (payload or {}).get("endpoint") or None
        hint = (payload or {}).get("hint", "") or ""
        force = bool((payload or {}).get("force", False))
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        src = Path(path).expanduser()
        if not src.exists():
            raise HTTPException(status_code=400, detail=f"path not found: {src}")
        cfg = _load_config()
        try:
            job = MANAGER.submit(cfg, str(src), endpoint, hint, force)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"id": job.id}, status_code=202)

    @app.get("/api/jobs")
    def list_jobs() -> JSONResponse:
        return JSONResponse([_job_to_dict(j) for j in MANAGER.list()])

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> JSONResponse:
        job = MANAGER.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return JSONResponse(_job_to_dict(job))

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

    @app.post("/api/validate")
    def validate(payload: dict = Body(default_factory=dict)) -> JSONResponse:  # noqa: B008
        raw = (payload or {}).get("path", "").strip()
        if not raw:
            return JSONResponse({"exists": False, "kind": None, "images": 0, "error": ""})
        try:
            p = Path(raw).expanduser().resolve(strict=False)
        except OSError as exc:
            return JSONResponse({"exists": False, "kind": None, "images": 0,
                                 "error": str(exc)})
        if not p.exists():
            return JSONResponse({"exists": False, "kind": None, "images": 0,
                                 "error": "does not exist"})
        if p.is_file():
            ok = p.suffix.lower() in IMAGE_EXTS
            return JSONResponse({
                "exists": True,
                "kind": "file",
                "images": 1 if ok else 0,
                "resolved": str(p),
                "error": "" if ok else f"unsupported extension {p.suffix}",
            })
        if p.is_dir():
            n = sum(1 for c in p.rglob("*")
                    if c.is_file() and c.suffix.lower() in IMAGE_EXTS)
            return JSONResponse({
                "exists": True,
                "kind": "dir",
                "images": n,
                "resolved": str(p),
                "error": "" if n > 0 else "no supported images found",
            })
        return JSONResponse({"exists": True, "kind": "other", "images": 0,
                             "resolved": str(p), "error": "not a file or folder"})

    @app.get("/api/browse")
    def browse(path: str = Query(default="")) -> JSONResponse:
        target = Path(path).expanduser() if path else BROWSE_ROOT
        try:
            target = target.resolve(strict=False)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not _under_root(target, BROWSE_ROOT):
            raise HTTPException(status_code=403, detail=f"outside {BROWSE_ROOT}")
        if not target.exists() or not target.is_dir():
            raise HTTPException(status_code=404, detail="not a directory")

        dirs: list[dict] = []
        files: list[dict] = []
        for child in sorted(target.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child), "kind": "dir"})
                elif child.is_file() and child.suffix.lower() in IMAGE_EXTS:
                    files.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "kind": "file",
                            "size": child.stat().st_size,
                        }
                    )
            except OSError:
                continue

        crumbs: list[dict] = []
        cursor = target
        while _under_root(cursor, BROWSE_ROOT):
            crumbs.append({"name": cursor.name or str(cursor), "path": str(cursor)})
            if cursor == BROWSE_ROOT:
                break
            cursor = cursor.parent
        crumbs.reverse()

        return JSONResponse(
            {
                "path": str(target),
                "root": str(BROWSE_ROOT),
                "crumbs": crumbs,
                "dirs": dirs,
                "files": files,
            }
        )

    @app.get("/api/config")
    def get_config() -> JSONResponse:
        cfg = _load_config()
        return JSONResponse(
            {
                "default_endpoint": cfg.default_endpoint,
                "max_keywords": cfg.max_keywords,
                "concurrency": cfg.concurrency,
                "endpoints": [
                    {
                        "name": name,
                        "url": ep.url,
                        "model": ep.model,
                        "has_api_key": bool(ep.api_key),
                    }
                    for name, ep in sorted(cfg.endpoints.items())
                ],
                "config_path": str(DEFAULT_CONFIG_PATH),
            }
        )

    @app.put("/api/config")
    def put_config(payload: dict = Body(default_factory=dict)) -> JSONResponse:  # noqa: B008
        try:
            _write_config(payload)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_config()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return _static_page("index.html")

    @app.get("/settings")
    def settings_page() -> FileResponse:
        return _static_page("settings.html")

    return app


def _static_page(name: str) -> FileResponse:
    html = STATIC_DIR / name
    if not html.exists():
        raise HTTPException(status_code=500, detail=f"{name} missing")
    return FileResponse(html, media_type="text/html")


def _under_root(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def _write_config(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    default_endpoint = str(payload.get("default_endpoint") or "").strip()
    if not default_endpoint:
        raise ValueError("default_endpoint is required")
    try:
        max_keywords = int(payload.get("max_keywords"))
        concurrency = int(payload.get("concurrency"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "max_keywords and concurrency must be integers"
        ) from exc
    if max_keywords <= 0 or concurrency <= 0:
        raise ValueError("max_keywords and concurrency must be positive")

    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise ValueError("at least one endpoint is required")
    existing_keys = _read_existing_api_keys(DEFAULT_CONFIG_PATH)
    names: set[str] = set()
    cleaned: list[dict] = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            raise ValueError("each endpoint must be an object")
        name = str(ep.get("name") or "").strip()
        url = str(ep.get("url") or "").strip()
        model = str(ep.get("model") or "").strip()
        # Preserve existing key when "api_key" is absent from payload; allow
        # explicit empty string to clear it.
        api_key = (
            str(ep["api_key"] or "") if "api_key" in ep else existing_keys.get(name, "")
        )
        if not name or not name.isidentifier():
            raise ValueError(f"invalid endpoint name: {name!r}")
        if name in names:
            raise ValueError(f"duplicate endpoint name: {name}")
        if not url or not model:
            raise ValueError(f"endpoint {name!r} requires url and model")
        names.add(name)
        cleaned.append({"name": name, "url": url, "model": model, "api_key": api_key})
    if default_endpoint not in names:
        raise ValueError(
            f"default_endpoint {default_endpoint!r} is not one of the configured endpoints"
        )

    path = DEFAULT_CONFIG_PATH
    existing_prompt = _read_prompt_block(path)
    lines: list[str] = [
        f'default_endpoint = "{default_endpoint}"',
        f"max_keywords = {max_keywords}",
        f"concurrency = {concurrency}",
        "",
    ]
    for ep in cleaned:
        lines.append(f"[endpoints.{ep['name']}]")
        lines.append(f'url = "{_toml_escape(ep["url"])}"')
        lines.append(f'model = "{_toml_escape(ep["model"])}"')
        lines.append(f'api_key = "{_toml_escape(ep["api_key"])}"')
        lines.append("")
    lines.append(existing_prompt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")


def _read_existing_api_keys(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError:
        return {}
    out: dict[str, str] = {}
    for name, ep in (data.get("endpoints") or {}).items():
        if isinstance(ep, dict) and isinstance(ep.get("api_key"), str):
            out[name] = ep["api_key"]
    return out


def _read_prompt_block(path: Path) -> str:
    """Return the `[prompt]` section verbatim from an existing config, or a default."""
    if path.exists():
        text = path.read_text()
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            text = ""
        if text:
            lines = text.splitlines()
            for idx, line in enumerate(lines):
                if line.strip() == "[prompt]":
                    return "\n".join(lines[idx:])
    # fallback to the default
    from magpie.config import DEFAULT_CONFIG_TOML

    marker = DEFAULT_CONFIG_TOML.find("[prompt]")
    return DEFAULT_CONFIG_TOML[marker:] if marker != -1 else ""


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


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


