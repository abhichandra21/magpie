"""Writes caption + keywords into JPEG/HEIC files via exiftool."""

from __future__ import annotations

import contextlib
from pathlib import Path

import exiftool

from magpie.tagger import TagResult

MAGPIE_MARKER_PREFIXES = ("magpie/", "ai-tagger/")


class WriterError(RuntimeError):
    """Raised when exiftool fails or the target path is invalid."""


class MetadataWriter:
    """Long-lived exiftool process for speed. Call close() when done."""

    def __init__(self) -> None:
        self._et: exiftool.ExifToolHelper | None = None

    def _helper(self) -> exiftool.ExifToolHelper:
        if self._et is None:
            self._et = exiftool.ExifToolHelper()
            self._et.run()
        return self._et

    def close(self) -> None:
        if self._et is not None:
            with contextlib.suppress(Exception):
                self._et.terminate()
            self._et = None

    def __enter__(self) -> MetadataWriter:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def already_tagged(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            meta = self._helper().get_tags(str(path), tags=["XMP:CreatorTool"])
        except exiftool.exceptions.ExifToolExecuteError as exc:
            raise WriterError(f"exiftool read failed for {path}: {exc}") from exc
        if not meta:
            return False
        tool = meta[0].get("XMP:CreatorTool") or ""
        if not isinstance(tool, str):
            return False
        return tool.startswith(MAGPIE_MARKER_PREFIXES)

    def write(self, path: Path, result: TagResult, model_id: str) -> None:
        if not path.exists():
            raise WriterError(f"file not found: {path}")
        tags = {
            "IPTC:Caption-Abstract": result.caption,
            "IPTC:Keywords": list(result.keywords),
            "XMP:Description": result.caption,
            "XMP:Subject": list(result.keywords),
            "XMP:CreatorTool": f"magpie/{model_id}",
        }
        try:
            self._helper().set_tags(str(path), tags=tags)
        except exiftool.exceptions.ExifToolExecuteError as exc:
            raise WriterError(f"exiftool write failed for {path}: {exc}") from exc
