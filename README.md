# magpie

> Magpies collect shiny things and line their nests with them. This tool collects captions and keywords for your photos and writes them into the file.

`magpie` is a local-first CLI + daemon that uses vision-capable LLMs (via any OpenAI-compatible `/v1/chat/completions` endpoint, e.g. a local Ollama) to tag JPEG/HEIC photos with IPTC captions and keywords, writing metadata directly into the files on disk. Lightroom Classic, Photos.app, Finder, and every other viewer then see the tags natively.

## Install

Requires Python 3.12+ and the `exiftool` binary.

```bash
# macOS
brew install exiftool

# clone and install
git clone https://github.com/abhichandra21/magpie.git
cd magpie
uv sync
uv run magpie --help
```

Smoke test:

```bash
make test
make lint
```

## Config

First run creates `~/.config/magpie/config.toml` from a default template. Edit it with `magpie config`.

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
model = "qwen2.5vl:72b"
api_key = ""

[prompt]
system = "You are an expert photo cataloger. You reply with strict JSON only — no prose, no markdown fences."
user_template = """Analyze this photograph. Return a JSON object with exactly these keys:
  "caption": a single sentence, max 120 characters, describing the scene clearly and factually.
  "keywords": an array of 15 to 25 lowercase keyword strings. No hashtags, no duplicates, no phrases longer than 3 words.

Context hint (optional, may be empty): {hint}
"""
```

## Usage

One-shot, file or folder (recursive), exits when done:

```bash
magpie tag ~/Pictures/some-folder/
magpie tag ~/Pictures/one-photo.jpg --hint "family trip to Taos"
magpie tag ~/Pictures/some-folder/ --force   # re-tag even if already tagged
```

Daemon, watches a folder and tags new JPEG/HEIC files as they're written:

```bash
magpie watch ~/Pictures/inbox/
```

Open the config in `$EDITOR`:

```bash
magpie config
```

Each run appends a CSV row per file to `~/.local/share/magpie/runs/YYYY-MM-DDTHH-MM-SS.csv` with columns `path, status, model, caption, keyword_count, duration_ms, error`.

## Idempotency

`magpie` writes `XMP:CreatorTool = magpie/<model-id>` on every tagged file, and skips any file whose `XMP:CreatorTool` already starts with `magpie/` (or the legacy `ai-tagger/`). Re-running on a folder is safe and cheap. Pass `--force` to override.

## How to point it at the Spark machine

The NVIDIA DGX Spark on your LAN can run a much larger vision model than your Mac. To use it:

1. On the Spark box, pull a vision-capable model in Ollama (e.g. `qwen2.5vl:72b`, `llama3.2:90b-vision`, etc.):

   ```bash
   ssh spark
   ollama pull qwen2.5vl:72b
   ```

2. Expose Ollama on the LAN (not just localhost):

   ```bash
   # in a persistent env file or systemd unit on the Spark box
   export OLLAMA_HOST=0.0.0.0:11434
   systemctl --user restart ollama   # or however the daemon is started
   ```

3. Confirm reachability from your Mac:

   ```bash
   curl -s http://192.168.1.75:11434/api/tags | jq '.models[].name'
   ```

4. Point `magpie` at it either via config (`default_endpoint = "spark"`) or via the env override for a single run:

   ```bash
   MAGPIE_ENDPOINT=spark magpie tag ~/Pictures/some-folder/
   ```

`magpie` only needs a reachable OpenAI-compatible URL — no provider-specific code.

## Layout

```
src/magpie/
  tagger.py    # async vision tagger (mockable via respx)
  writer.py    # IPTC/XMP writer around pyexiftool
  runner.py    # async BatchRunner with concurrency + CSV log
  watcher.py   # watchdog-based folder daemon with debounce + backoff
  config.py    # TOML config + pydantic validation
  cli.py       # typer CLI (tag / watch / config)
```

## Not in v1

RAW files (CR3/RAF/NEF), SQLite state, GPS lookup, cloud providers (OpenAI/Gemini), GUI. The endpoint config is provider-neutral; pointing at a cloud endpoint works if you ever want that, but there is no provider-specific code path.
