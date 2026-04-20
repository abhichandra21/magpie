from pathlib import Path

import pytest

from magpie.config import DEFAULT_CONFIG_TOML, Config, ConfigError


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_load_creates_default_when_missing(tmp_path):
    cfg_path = tmp_path / "cfg.toml"
    cfg = Config.load(cfg_path)
    assert cfg_path.exists()
    assert cfg.default_endpoint == "mac"
    assert cfg.max_keywords == 25
    assert "mac" in cfg.endpoints and "spark" in cfg.endpoints


def test_env_override_picks_named_endpoint(tmp_path, monkeypatch):
    cfg_path = _write(tmp_path / "cfg.toml", DEFAULT_CONFIG_TOML)
    cfg = Config.load(cfg_path)
    monkeypatch.setenv("MAGPIE_ENDPOINT", "spark")
    assert cfg.endpoint().model == "qwen2.5vl:72b"


def test_explicit_name_overrides_env(tmp_path, monkeypatch):
    cfg_path = _write(tmp_path / "cfg.toml", DEFAULT_CONFIG_TOML)
    cfg = Config.load(cfg_path)
    monkeypatch.setenv("MAGPIE_ENDPOINT", "spark")
    assert cfg.endpoint("mac").url.startswith("http://localhost")


def test_unknown_endpoint_raises(tmp_path):
    cfg_path = _write(tmp_path / "cfg.toml", DEFAULT_CONFIG_TOML)
    cfg = Config.load(cfg_path)
    with pytest.raises(ConfigError):
        cfg.endpoint("nope")


def test_malformed_toml_raises(tmp_path):
    cfg_path = _write(tmp_path / "cfg.toml", "this is = not [valid")
    with pytest.raises(ConfigError):
        Config.load(cfg_path)


def test_schema_violation_raises(tmp_path):
    cfg_path = _write(
        tmp_path / "cfg.toml",
        'default_endpoint = "mac"\nmax_keywords = 0\n[endpoints.mac]\n'
        'url = "x"\nmodel = "y"\n[prompt]\nsystem = "s"\nuser_template = "u {hint}"\n',
    )
    with pytest.raises(ConfigError):
        Config.load(cfg_path)
