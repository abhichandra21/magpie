# magpie

Local-first CLI + daemon that uses vision-capable LLMs to tag JPEG/HEIC photos with IPTC captions and keywords, writing metadata directly into the files on disk.

Usage, config, and Spark-endpoint setup will be documented at the end of milestone 7.

## Quickstart

```bash
uv sync
uv run magpie --help
```

## Requires

- Python 3.12+
- `exiftool` binary (`brew install exiftool`)
- A reachable OpenAI-compatible vision endpoint (e.g. local Ollama)
