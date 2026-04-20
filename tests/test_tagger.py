import base64
import io
import json
from typing import Any

import httpx
import pytest
import respx
from PIL import Image

from magpie.tagger import EndpointConfig, PromptConfig, Tagger, TagResult


def _jpeg_bytes(size: tuple[int, int] = (200, 200)) -> bytes:
    img = Image.new("RGB", size, color=(120, 200, 40))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _big_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (3000, 2000), color=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _chat_response(content: str) -> dict[str, Any]:
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


def _make_tagger(max_keywords: int = 25) -> Tagger:
    endpoint = EndpointConfig(
        url="http://endpoint.test/v1",
        model="test-model",
        api_key="",
    )
    prompt = PromptConfig(
        system="You reply with strict JSON.",
        user_template="Analyze. Hint: {hint}",
    )
    return Tagger(endpoint=endpoint, prompt=prompt, max_keywords=max_keywords)


@pytest.mark.asyncio
@respx.mock
async def test_tag_parses_valid_response():
    payload = {
        "caption": "A dog on grass",
        "keywords": ["Dog", "grass", "outdoors", "dog"],
    }
    route = respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )
    tagger = _make_tagger()

    result = await tagger.tag(_jpeg_bytes(), hint="park visit")

    assert isinstance(result, TagResult)
    assert result.caption == "A dog on grass"
    # dedupe (case-insensitive) + lowercased
    assert result.keywords == ["dog", "grass", "outdoors"]
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_tag_retries_once_on_non_json():
    good = {"caption": "ok", "keywords": ["a", "b"]}
    route = respx.post("http://endpoint.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_chat_response("Sure, here: not json!")),
            httpx.Response(200, json=_chat_response(json.dumps(good))),
        ]
    )
    tagger = _make_tagger()
    result = await tagger.tag(_jpeg_bytes())
    assert route.call_count == 2
    assert result.caption == "ok"
    assert result.keywords == ["a", "b"]

    # second call should have appended a retry user message
    second_body = json.loads(route.calls[1].request.content.decode())
    roles = [m["role"] for m in second_body["messages"]]
    assert roles.count("user") >= 2


@pytest.mark.asyncio
@respx.mock
async def test_tag_raises_after_two_bad_replies():
    respx.post("http://endpoint.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_chat_response("garbage one")),
            httpx.Response(200, json=_chat_response("garbage two")),
        ]
    )
    tagger = _make_tagger()
    with pytest.raises(ValueError):
        await tagger.tag(_jpeg_bytes())


@pytest.mark.asyncio
@respx.mock
async def test_tag_trims_to_max_keywords():
    payload = {
        "caption": "scene",
        "keywords": [f"kw{i}" for i in range(40)],
    }
    respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )
    tagger = _make_tagger(max_keywords=5)
    result = await tagger.tag(_jpeg_bytes())
    assert len(result.keywords) == 5


@pytest.mark.asyncio
@respx.mock
async def test_tag_accepts_json_fenced_in_markdown():
    payload = {"caption": "scene", "keywords": ["x"]}
    fenced = f"```json\n{json.dumps(payload)}\n```"
    respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(fenced))
    )
    tagger = _make_tagger()
    result = await tagger.tag(_jpeg_bytes())
    assert result.caption == "scene"
    assert result.keywords == ["x"]


@pytest.mark.asyncio
@respx.mock
async def test_tag_downscales_large_image():
    payload = {"caption": "c", "keywords": []}
    route = respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )
    tagger = _make_tagger()
    await tagger.tag(_big_jpeg_bytes())

    body = json.loads(route.calls[0].request.content.decode())
    msg = body["messages"][-1]
    image_block = next(part for part in msg["content"] if part.get("type") == "image_url")
    url = image_block["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    b64 = url.split(",", 1)[1]
    decoded = base64.b64decode(b64)
    img = Image.open(io.BytesIO(decoded))
    assert max(img.size) <= 1568
    assert len(decoded) < len(_big_jpeg_bytes())


@pytest.mark.asyncio
@respx.mock
async def test_tag_sends_hint_in_prompt():
    payload = {"caption": "c", "keywords": []}
    route = respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )
    tagger = _make_tagger()
    await tagger.tag(_jpeg_bytes(), hint="beach trip")

    body = json.loads(route.calls[0].request.content.decode())
    user_msg = next(m for m in body["messages"] if m["role"] == "user")
    text_part = next(p for p in user_msg["content"] if p.get("type") == "text")
    assert "beach trip" in text_part["text"]


@pytest.mark.asyncio
@respx.mock
async def test_tag_sends_bearer_auth_when_api_key_set():
    payload = {"caption": "c", "keywords": []}
    route = respx.post("http://endpoint.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(json.dumps(payload)))
    )
    endpoint = EndpointConfig(
        url="http://endpoint.test/v1", model="test-model", api_key="sk-secret"
    )
    prompt = PromptConfig(system="s", user_template="u {hint}")
    tagger = Tagger(endpoint=endpoint, prompt=prompt, max_keywords=10)
    await tagger.tag(_jpeg_bytes())
    auth = route.calls[0].request.headers.get("authorization")
    assert auth == "Bearer sk-secret"
