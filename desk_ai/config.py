"""Configuration. Precedence: defaults < ~/.config/desk-ai/config.toml < environment."""
import os
import pathlib
import tomllib
from dataclasses import dataclass, field, fields


def config_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "desk-ai"


@dataclass
class Config:
    # provider
    base_url: str = "https://integrate.api.nvidia.com/v1"
    model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    fallback_models: list = field(default_factory=list)
    api_key_file: str = ""  # empty -> ~/.config/desk-ai/api_key

    # generation
    temperature: float = 0.4
    max_tokens: int = 1024
    extra_system_prompt: str = ""  # appended to the built-in system prompt

    # behaviour
    max_attempts: int = 4  # per model, for transient provider errors
    max_tool_rounds: int = 0  # 0 = unlimited tool runs per turn
    command_timeout: int = 30  # seconds, for run_command
    approval_timeout: int = 120  # seconds to wait for Allow/Deny
    tool_round_warn_every: int = 25  # pause + offer resume/stop every N tool runs

    # window
    window_width: int = 460
    window_height: int = 600

    # filled in by load(); not a setting
    warnings: list = field(default_factory=list)


def _type_ok(value, expected) -> bool:
    if expected is bool:
        return isinstance(value, bool)
    if expected in (int, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is list:
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return isinstance(value, expected)


def load() -> Config:
    cfg = Config()
    path = config_dir() / "config.toml"
    data = {}
    if path.exists():
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as e:
            cfg.warnings.append(f"config.toml ignored: {e}")

    known = {f.name: f for f in fields(Config) if f.name != "warnings"}
    for key, value in data.items():
        f = known.get(key)
        if f is None:
            cfg.warnings.append(f"config.toml: unknown setting '{key}' (ignored)")
        elif not _type_ok(value, f.type):
            cfg.warnings.append(
                f"config.toml: '{key}' should be {f.type.__name__}, got {type(value).__name__} (ignored)")
        else:
            setattr(cfg, key, float(value) if f.type is float else value)

    # environment wins over the file
    cfg.base_url = os.environ.get("NVIDIA_BASE_URL", cfg.base_url)
    cfg.model = os.environ.get("NVIDIA_MODEL", cfg.model)
    fallback = os.environ.get("NVIDIA_FALLBACK_MODELS")
    if fallback is not None:
        cfg.fallback_models = [m.strip() for m in fallback.split(",") if m.strip()]
    return cfg


def load_api_key(cfg: Config):
    key = os.environ.get("NVIDIA_API_KEY")
    if key and key.strip():
        return key.strip()
    path = pathlib.Path(cfg.api_key_file).expanduser() if cfg.api_key_file else config_dir() / "api_key"
    try:
        return path.read_text().strip() or None
    except OSError:
        return None
