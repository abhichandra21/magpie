# magpie — Project Bootstrap Prompt

> Magpies collect shiny things and line their nests with them. This tool collects captions and keywords for your photos and writes them into the file.

**Repo:** https://github.com/abhichandra21/magpie.git

Clone the repo, drop this file in as `PROMPT.md`, start Claude Code there, and paste the contents of the "Prompt for Claude" section below as the first message. The "Design Spec" section is reference material the prompt points to.

```bash
git clone https://github.com/abhichandra21/magpie.git
cd magpie
# copy this file in as PROMPT.md, then:
claude
```

---

## Prompt for Claude

Build `magpie`, a local-first CLI + daemon that uses vision-capable LLMs to tag JPEG/HEIC photos with IPTC captions and keywords, writing metadata directly into the files on disk. This replaces a Lightroom plugin workflow — no Lightroom, no cloud, just files and a local Ollama endpoint.

Follow the design spec below exactly. If anything is unclear or seems wrong, ASK before deviating. Use TDD: write a failing test for each component before the implementation. Keep components isolated and independently testable, per the boundaries in the spec.

Stack: Python 3.12+, `uv` for dependency management, `ruff` for lint/format, `pytest` for tests. Dependencies: `httpx` (async HTTP), `pyexiftool`, `watchdog`, `typer` (CLI), `rich` (progress bars), `pillow` (downscale), `tomli`/`tomllib`. System dep: `exiftool` binary (assume installed; fail clearly if missing).

Initial milestones in order — stop after each one for review:

1. Project skeleton: `pyproject.toml` with uv, `ruff.toml`, `pytest.ini`, `src/magpie/` package layout, placeholder CLI that prints help. Add `Makefile` with `make test`, `make lint`, `make run`. Commit.
2. `Tagger` component + tests. Pure function, no file I/O. Mock the HTTP endpoint with `respx`. Commit.
3. `MetadataWriter` component + tests against real sample JPEGs in `tests/fixtures/` (include a JPEG with existing metadata and one without). Commit.
4. `BatchRunner` + tests. Async concurrency, resume, CSV logging. Commit.
5. `Watcher` + tests using a temp dir and `watchdog`'s polling observer for determinism. Commit.
6. CLI wiring (`tag`, `watch`, `config` subcommands) + integration test that runs end-to-end against a mocked endpoint. Commit.
7. README with install, config, usage examples, and a "how to point it at the Spark machine" section.

Do not create any files that are not required for execution or testing. Do not write summary/changelog markdown files. Do not commit without asking, and never to `main`/`master` directly — work on a feature branch.

---

## Design Spec

### Goal

Replace the "AI Image Tagger for Lightroom Classic" plugin workflow with a file-on-disk CLI + daemon. User selects JPEG/HEIC folders (or drops files into a watched inbox), tool generates caption + keywords via a local vision LLM, writes IPTC/XMP metadata directly into the file. Lightroom, Photos.app, Finder, and every other viewer then see the metadata natively.

### Non-goals (YAGNI — do not build these)

- RAW file support (CR3, RAF, NEF, etc.)
- SQLite state database
- Description/Title fields beyond caption
- GPS / location lookup (caller supplies `--hint` when they want context)
- GUI
- Cloud providers (OpenAI, Gemini) — the endpoint config supports them structurally, but do not add any provider-specific code paths

### Architecture

One Python package, two entry points:

```
magpie tag <path>          # one-shot, file or folder (recursive), exit when done
magpie watch <path>...     # daemon, watches folder(s), tags new JPEGs as they appear
magpie config              # open $EDITOR on config file
```

### Components

Each component lives in its own module, has a single clear responsibility, and is independently unit-testable.

1. **`magpie.tagger.Tagger`**
   - Signature: `async def tag(image_bytes: bytes, hint: str = "") -> TagResult`
   - `TagResult = {caption: str, keywords: list[str]}`
   - Downscales image to max 1568px longest side (Pillow), base64-encodes, POSTs to configured OpenAI-compatible `/chat/completions` endpoint with vision content block.
   - Parses JSON response. On parse failure, retries ONCE with an additional message: `"Your previous reply was not valid JSON. Reply with ONLY a JSON object, no prose, no markdown fences."`
   - Dedupes keywords case-insensitively, lowercases all, trims to config `max_keywords`.
   - No file I/O. No knowledge of exiftool. Fully mockable with `respx`.

2. **`magpie.writer.MetadataWriter`**
   - Wraps `pyexiftool.ExifToolHelper` (reuse one long-lived process for speed).
   - `already_tagged(path) -> bool`: returns True iff `XMP:CreatorTool` starts with `ai-tagger/` or `magpie/`. (Accept `ai-tagger/` for forward compatibility if the marker prefix ever changes.)
   - `write(path, result: TagResult, model_id: str)`: writes `IPTC:Caption-Abstract`, `IPTC:Keywords` (list), `XMP:dc:description`, `XMP:dc:subject` (list), `XMP:CreatorTool=magpie/<model_id>`. Preserves exiftool's default `_original` backup on first write.
   - Atomic: if exiftool exits non-zero, leave the file untouched and raise.

3. **`magpie.runner.BatchRunner`**
   - Walks path (recursive), filters `*.jpg|*.jpeg|*.heic|*.heif` (case-insensitive).
   - Async concurrency with semaphore (default 2, configurable).
   - For each file: check `already_tagged` → skip; else call Tagger → Writer → append CSV row.
   - CSV: `~/.local/share/magpie/runs/YYYY-MM-DDTHH-MM-SS.csv` with columns `path, status, model, caption, keyword_count, duration_ms, error`.
   - Rich progress bar. `Ctrl-C` finishes in-flight tasks and exits cleanly.
   - `--force` flag: skip the `already_tagged` check.

4. **`magpie.watcher.Watcher`**
   - `watchdog` `Observer`, one or more watched dirs.
   - On file create/modify: wait until file size is stable for 2 seconds (handle in-progress writes), then enqueue to a `BatchRunner` task queue.
   - On endpoint error: exponential backoff (1s, 2s, 4s, ..., cap 60s), keep retrying forever. Log each failure to stderr + run CSV.
   - Clean shutdown on SIGINT/SIGTERM.

5. **`magpie.config.Config`**
   - Loads `~/.config/magpie/config.toml` (create from default template on first run).
   - Schema validation (pydantic v2 or dataclass + manual). Fail with a clear error message on malformed config.
   - Env var override: `MAGPIE_ENDPOINT=spark magpie tag ./foo` picks the `[endpoints.spark]` block.

### Default config

```toml
default_endpoint = "mac"
max_keywords = 25
concurrency = 2

[endpoints.mac]
url = "http://localhost:11434/v1"
model = "gemma4:26b-a4b-it-q4_K_M"
api_key = ""

[endpoints.spark]
url = "http://192.168.1.75:11434/v1"
model = "qwen2.5vl:72b"  # user pulls this manually; magpie does not manage models
api_key = ""

[prompt]
system = "You are an expert photo cataloger. You reply with strict JSON only — no prose, no markdown fences."
user_template = """Analyze this photograph. Return a JSON object with exactly these keys:
  "caption": a single sentence, max 120 characters, describing the scene clearly and factually.
  "keywords": an array of 15 to 25 lowercase keyword strings. No hashtags, no duplicates, no phrases longer than 3 words.

Context hint (optional, may be empty): {hint}
"""
```

### Data flow (single photo)

```
JPEG on disk
  → MetadataWriter.already_tagged(path)?  → skipped, logged
  → read bytes, Pillow downscale to 1568px long edge, JPEG quality 85
  → base64 encode
  → Tagger.tag(bytes, hint) via httpx AsyncClient → TagResult
  → MetadataWriter.write(path, result, model_id)
  → CSV append
```

### Error handling

| Failure | `tag` mode | `watch` mode |
|---|---|---|
| Endpoint unreachable | fail fast, exit 1 after current batch | log + exponential backoff, keep running |
| Model returns non-JSON twice | log error, mark file failed, continue | same |
| exiftool write fails | log error, file untouched, continue | same |
| exiftool binary missing | fail at startup with clear message pointing to `brew install exiftool` | same |
| Ctrl-C | finish in-flight photos, exit 0 | same |

### Idempotency

Marker-based: `XMP:CreatorTool` starts with `magpie/` → skip unless `--force`. No separate state store. Re-running on a folder is safe and cheap.

### Testing strategy

- Unit tests for each component in isolation.
- `Tagger` tests: `respx`-mocked endpoint, cover JSON parse failure + retry path.
- `MetadataWriter` tests: real JPEGs in `tests/fixtures/`, verify round-trip with exiftool.
- `BatchRunner` tests: monkeypatch Tagger + Writer, verify skip logic, concurrency, CSV output.
- `Watcher` tests: `watchdog.observers.polling.PollingObserver` for determinism, tmp dir, verify debounce.
- End-to-end CLI test: `typer.testing.CliRunner`, mocked endpoint, real exiftool.
- Target: 85%+ line coverage, measured via `pytest-cov`. No coverage gates that block merges — it's a signal, not a gate.

### Repo layout

```
magpie/
├── pyproject.toml
├── ruff.toml
├── pytest.ini
├── Makefile
├── README.md
├── src/magpie/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── tagger.py
│   ├── writer.py
│   ├── runner.py
│   └── watcher.py
└── tests/
    ├── fixtures/
    │   ├── untagged.jpg
    │   └── already_tagged.jpg
    ├── test_tagger.py
    ├── test_writer.py
    ├── test_runner.py
    ├── test_watcher.py
    └── test_cli.py
```

### Runtime environment

- macOS (primary): Apple M5 Pro, 64 GB unified memory, Ollama at `http://localhost:11434`, Gemma 4 26B MoE vision model (`gemma4:26b-a4b-it-q4_K_M`).
- Optional secondary endpoint: NVIDIA DGX Spark at `192.168.1.75`, accessible over LAN via `ssh spark`. User can pull any Ollama vision model there (Qwen2.5-VL 72B, Llama 3.2 90B Vision, etc.) for higher-quality tagging. To expose that Ollama over LAN the user sets `OLLAMA_HOST=0.0.0.0:11434` on the Spark box. `magpie` just needs a reachable URL.
- Both endpoints speak the OpenAI-compatible `/v1/chat/completions` API. No provider-specific code.

### Out of scope for v1 but keep the door open

- Extra output fields (title, description) — leave the prompt template extensible.
- RAW support — do not add now, but don't write code that hardcodes "JPEG only" assumptions deeper than the file-extension filter in `BatchRunner`.
- Additional backends — the endpoint config already generalizes; no work required.

### Success criteria

1. `magpie tag ~/Pictures/some-folder/` tags every JPEG in that folder with caption + keywords, in-place, with an exiftool `_original` backup of each file.
2. Re-running the same command is a no-op (all files skipped).
3. `magpie watch ~/Pictures/inbox/` tags new JPEGs dropped in that folder within ~10 seconds of the file being fully written.
4. `MAGPIE_ENDPOINT=spark magpie tag ...` uses the Spark endpoint without code changes.
5. Every component has a test file. `make test` and `make lint` both pass.
