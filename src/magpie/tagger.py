"""Pure tagging logic: bytes in, TagResult out. No file I/O, no exiftool."""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field

import httpx
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

MAX_LONG_EDGE_PX = 1568
JPEG_QUALITY = 85
DEFAULT_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class EndpointConfig:
    url: str  # base URL ending in /v1
    model: str
    api_key: str = ""


@dataclass(frozen=True)
class PromptConfig:
    system: str
    user_template: str  # must contain "{hint}"


@dataclass(frozen=True)
class TagResult:
    caption: str
    keywords: list[str] = field(default_factory=list)


class Tagger:
    def __init__(
        self,
        endpoint: EndpointConfig,
        prompt: PromptConfig,
        max_keywords: int = 25,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._endpoint = endpoint
        self._prompt = prompt
        self._max_keywords = max_keywords
        self._client = client
        self._timeout_s = timeout_s

    async def tag(self, image_bytes: bytes, hint: str = "") -> TagResult:
        data_url = _image_to_data_url(image_bytes)
        user_text = self._prompt.user_template.format(hint=hint or "")

        messages: list[dict] = [
            {"role": "system", "content": self._prompt.system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

        content = await self._call(messages)
        parsed = _try_parse_json(content)
        if parsed is None:
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid JSON. "
                        "Reply with ONLY a JSON object, no prose, no markdown fences."
                    ),
                }
            )
            content = await self._call(messages)
            parsed = _try_parse_json(content)
        if parsed is None:
            raise ValueError("Tagger: endpoint returned non-JSON twice")

        return self._build_result(parsed)

    def _build_result(self, parsed: dict) -> TagResult:
        caption = str(parsed.get("caption", "")).strip()
        raw_keywords = parsed.get("keywords") or []
        seen: set[str] = set()
        cleaned: list[str] = []
        for kw in raw_keywords:
            if not isinstance(kw, str):
                continue
            norm = kw.strip().lower()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            cleaned.append(norm)
        return TagResult(caption=caption, keywords=cleaned[: self._max_keywords])

    async def _call(self, messages: list[dict]) -> str:
        payload = {
            "model": self._endpoint.model,
            "messages": messages,
            "stream": False,
        }
        headers = {}
        if self._endpoint.api_key:
            headers["Authorization"] = f"Bearer {self._endpoint.api_key}"

        url = self._endpoint.url.rstrip("/") + "/chat/completions"

        if self._client is not None:
            resp = await self._client.post(
                url, json=payload, headers=headers, timeout=self._timeout_s
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _image_to_data_url(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        long_edge = max(w, h)
        if long_edge > MAX_LONG_EDGE_PX:
            scale = MAX_LONG_EDGE_PX / long_edge
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _try_parse_json(text: str) -> dict | None:
    if not text:
        return None
    candidates = [text.strip()]
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    # also try substring from first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and "caption" in obj:
            return obj
    return None
