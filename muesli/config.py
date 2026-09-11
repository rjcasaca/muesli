from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "muesli"
CONFIG_PATH = CONFIG_DIR / "config.toml"
ENV_PATH = CONFIG_DIR / "env"
USER_TEMPLATES = CONFIG_DIR / "templates"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "muesli"
USAGE_LOG = STATE_DIR / "usage.jsonl"
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


# Built-in enhance backends. "custom" runs `command` through the shell instead.
ENHANCE_BACKENDS = ("claude", "grok", "ollama", "custom")


@dataclass
class EnhanceConfig:
    backend: str = "claude"
    model: str = ""
    command: str = 'claude -p < "{prompt_file}" > "{output}"'
    default_template: str = "general"
    default_tone: str = "concise"
    tones: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TONES))


# Rough list prices in USD per hour of audio, keyed by STT model, falling back to provider.
DEFAULT_STT_PER_HOUR = {
    "whisper-large-v3-turbo": 0.04,
    "whisper-large-v3": 0.111,
    "whisper-1": 0.36,
    "gpt-4o-transcribe": 0.36,
    "gpt-4o-mini-transcribe": 0.18,
    "xai": 0.10,
    "local": 0.0,
}


@dataclass
class CostsConfig:
    stt_per_hour: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_STT_PER_HOUR))
    # Used only when the enhance backend does not report cost itself (everything but claude).
    llm_input_per_mtok: float = 3.0
    llm_output_per_mtok: float = 15.0

    def stt_hourly(self, provider: str, model: str) -> float:
        return float(self.stt_per_hour.get(model, self.stt_per_hour.get(provider, 0.0)))


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
    costs: CostsConfig = field(default_factory=CostsConfig)

    def env(self, name: str) -> str:
        val = os.environ.get(name, "")
        if not val:
            raise RuntimeError(f"environment variable {name} is not set")
        return val

    def as_dict(self) -> dict:
        d = asdict(self)
        d["notes_dir"] = str(self.notes_dir)
        return d


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
    if "backend" not in enh and enh.get("command", EnhanceConfig.command) != EnhanceConfig.command:
        cfg.enhance.backend = "custom"  # pre-0.3 config with a hand-written command: keep running that
    cfg.enhance.tones.update(enh.get("tones", {}))
    costs = raw.get("costs", {})
    _apply(cfg.costs, costs)
    cfg.costs.stt_per_hour.update({k: float(v) for k, v in costs.get("stt_per_hour", {}).items()})
    return cfg


# ---------- env file (API keys) ----------
def read_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    """KEY=value lines, systemd EnvironmentFile style. Values are returned but callers should only report presence."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def write_env_value(name: str, value: str, path: Path = ENV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    pat = re.compile(rf"^\s*(export\s+)?{re.escape(name)}\s*=")
    new = f"{name}={value}"
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = new
            break
    else:
        lines.append(new)
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


# ---------- in-place TOML editing ----------
def toml_literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_literal(v) for v in value) + "]"
    if isinstance(value, Path):
        value = str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _trailing_comment(rest: str) -> str:
    """Return the ' # comment' part of a `key = value # comment` line, ignoring '#' inside strings."""
    quote = ""
    for i, ch in enumerate(rest):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return rest[i:].rstrip()
    return ""


def set_config_value(key: str, value, path: Path = CONFIG_PATH) -> None:
    """Set `section.leaf` (or a top-level `leaf`) in config.toml, preserving everything else including comments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    section, _, leaf = key.rpartition(".")
    literal = toml_literal(value)
    key_re = re.compile(rf"^(\s*)(?:{re.escape(leaf)}|\"{re.escape(leaf)}\")\s*=\s*(.*)$")

    # locate the section's line range
    start, end = 0, len(lines)
    if section:
        start = -1
        for i, line in enumerate(lines):
            if line.strip() == f"[{section}]":
                start = i + 1
                break
        if start == -1:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{section}]", f"{leaf} = {literal}"]
            path.write_text("\n".join(lines) + "\n")
            return
    for i in range(start, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            break
    else:
        end = len(lines)
    if not section:  # top level ends at the first table header
        end = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))

    for i in range(start, end):
        m = key_re.match(lines[i])
        if m:
            comment = _trailing_comment(m.group(2))
            lines[i] = f"{m.group(1)}{leaf} = {literal}" + (f"  {comment}" if comment else "")
            break
    else:
        insert_at = end
        while insert_at > start and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"{leaf} = {literal}")
    path.write_text("\n".join(lines) + "\n")
    tomllib.loads(path.read_text())  # raise if we somehow produced invalid TOML


def flatten(d: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        name = f"{prefix}{k}"
        if isinstance(v, dict) and k not in ("tones", "stt_per_hour"):
            out.update(flatten(v, name + "."))
        else:
            out[name] = v
    return out
