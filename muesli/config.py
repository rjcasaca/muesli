from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "muesli"
CONFIG_PATH = CONFIG_DIR / "config.toml"
USER_TEMPLATES = CONFIG_DIR / "templates"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
SOCKET_PATH = RUNTIME_DIR / "muesli.sock"


@dataclass
class SttConfig:
    provider: str = "openai"
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "whisper-large-v3-turbo"
    api_key_env: str = "GROQ_API_KEY"
    xai_api_key_env: str = "XAI_API_KEY"
    local_engine: str = "faster-whisper"
    local_model: str = "small"
    local_device: str = "auto"
    local_compute: str = "int8"
    whisper_cpp_bin: str = "whisper-cli"


@dataclass
class DetectConfig:
    enabled: bool = True
    apps: list[str] = field(
        default_factory=lambda: [
            "teams", "zoom", "chrome", "chromium", "brave", "firefox", "slack", "discord", "webex", "meet",
        ]
    )
    auto_start: bool = False
    auto_stop_seconds: int = 60
    notify: bool = True


DEFAULT_TONES = {
    "concise": "Be terse. Prefer bullets over prose. No filler, no restating the obvious.",
    "formal": "Professional, neutral tone suitable for sharing with clients or management.",
    "casual": "Relaxed, first person, like notes written to yourself.",
    "detailed": "Preserve nuance. Include supporting detail and short direct quotes where they add precision.",
}


@dataclass
class EnhanceConfig:
    command: str = 'claude -p < "{prompt_file}" > "{output}"'
    default_template: str = "general"
    default_tone: str = "concise"
    tones: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TONES))


@dataclass
class Config:
    notes_dir: Path = Path("~/notes/meetings").expanduser()
    language: str = ""
    chunk_seconds: int = 30
    silence_peak: int = 400
    vocabulary: list[str] = field(default_factory=list)
    stt: SttConfig = field(default_factory=SttConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    enhance: EnhanceConfig = field(default_factory=EnhanceConfig)

    def env(self, name: str) -> str:
        val = os.environ.get(name, "")
        if not val:
            raise RuntimeError(f"environment variable {name} is not set")
        return val


def _apply(obj, raw: dict) -> None:
    for key, val in raw.items():
        if hasattr(obj, key) and not isinstance(getattr(obj, key), dict):
            setattr(obj, key, val)


def load_config(path: Path = CONFIG_PATH) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg
    raw = tomllib.loads(path.read_text())
    if "notes_dir" in raw:
        cfg.notes_dir = Path(raw["notes_dir"]).expanduser()
    for key in ("language", "chunk_seconds", "silence_peak", "vocabulary"):
        if key in raw:
            setattr(cfg, key, raw[key])
    _apply(cfg.stt, raw.get("stt", {}))
    _apply(cfg.detect, raw.get("detect", {}))
    enh = raw.get("enhance", {})
    _apply(cfg.enhance, enh)
    cfg.enhance.tones.update(enh.get("tones", {}))
    return cfg
