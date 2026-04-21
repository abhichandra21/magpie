"""Config loading + defaults. Reads ~/.config/magpie/config.toml."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from magpie.tagger import EndpointConfig, PromptConfig

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "magpie" / "config.toml"

DEFAULT_CONFIG_TOML = """\
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

[libraries]
# Add named photo libraries to browse from the web UI. Paths may use ~.
# Example:
#   pictures = "~/Pictures"
#   lightroom = "~/NextCloud/Lightroom Publish"

[prompt]
system = "You are an expert photo cataloger. You reply with strict JSON only \u2014 no prose, no markdown fences."
user_template = \"\"\"Analyze this photograph. Return a JSON object with exactly these keys:
  \"caption\": a single sentence, max 120 characters, describing the scene clearly and factually.
  \"keywords\": an array of 15 to 25 lowercase keyword strings. No hashtags, no duplicates, no phrases longer than 3 words.

Context hint (optional, may be empty): {hint}
\"\"\"
"""


class ConfigError(RuntimeError):
    pass


class _EndpointModel(BaseModel):
    url: str
    model: str
    api_key: str = ""


class _PromptModel(BaseModel):
    system: str
    user_template: str


class _ConfigModel(BaseModel):
    default_endpoint: str = "mac"
    max_keywords: int = Field(default=25, gt=0)
    concurrency: int = Field(default=2, gt=0)
    endpoints: dict[str, _EndpointModel]
    prompt: _PromptModel
    libraries: dict[str, str] = Field(default_factory=dict)


class Config:
    def __init__(
        self,
        default_endpoint: str,
        max_keywords: int,
        concurrency: int,
        endpoints: dict[str, EndpointConfig],
        prompt: PromptConfig,
        libraries: dict[str, Path] | None = None,
    ) -> None:
        self.default_endpoint = default_endpoint
        self.max_keywords = max_keywords
        self.concurrency = concurrency
        self.endpoints = endpoints
        self.prompt = prompt
        self.libraries = libraries or {}

    def endpoint(self, name: str | None = None) -> EndpointConfig:
        key = name or os.environ.get("MAGPIE_ENDPOINT") or self.default_endpoint
        if key not in self.endpoints:
            raise ConfigError(
                f"endpoint '{key}' not defined; available: {sorted(self.endpoints)}"
            )
        return self.endpoints[key]

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or DEFAULT_CONFIG_PATH
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG_TOML)
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config.toml is not valid TOML: {exc}") from exc
        try:
            model = _ConfigModel.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"config.toml failed validation: {exc}") from exc
        return cls(
            default_endpoint=model.default_endpoint,
            max_keywords=model.max_keywords,
            concurrency=model.concurrency,
            endpoints={
                k: EndpointConfig(url=v.url, model=v.model, api_key=v.api_key)
                for k, v in model.endpoints.items()
            },
            prompt=PromptConfig(
                system=model.prompt.system,
                user_template=model.prompt.user_template,
            ),
            libraries={
                name: Path(raw).expanduser()
                for name, raw in model.libraries.items()
            },
        )
