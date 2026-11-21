"""Configuration. Precedence: defaults < ~/.config/ordis/config.toml < environment."""
import os
import pathlib
import tomllib
import httpx
from dataclasses import dataclass, field, fields
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


def config_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "ordis"


@dataclass
class Config:
    # provider (any OpenAI-compatible /v1 endpoint)
    base_url: str = DEFAULT_BASE_URL
    model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    fallback_models: list = field(default_factory=list)
    api_key_file: str = ""  # empty -> ~/.config/ordis/api_key

    # generation
    temperature: float = 0.4
    max_tokens: int = 4096  # reasoning models spend this on thinking too; file edits need room
    # system prompt, highest priority first: system_prompt_file > system_prompt > built-in default.
    # extra_system_prompt is always appended. skill_file is appended last (after extra_system_prompt).
    # {tools} {date} {home} {app_dir} {config_file} {model} are filled in; see ordis/prompt.py.
    system_prompt_file: str = ""  # re-read every turn, so you can tune it without restarting
    system_prompt: str = ""
    extra_system_prompt: str = ""
    skill_file: str = ""  # re-read every turn; appended after extra_system_prompt

    # behaviour
    max_attempts: int = 4  # per model, for transient provider errors
    max_tool_rounds: int = 0  # 0 = unlimited tool runs per turn
    command_timeout: int = 30  # seconds, for run_command
    approval_timeout: int = 120  # seconds to wait for Allow/Deny
    tool_round_warn_every: int = 25  # pause + offer resume/stop every N tool runs

    # window
    window_width: int = 460
    window_height: int = 600
    render_markdown: bool = True  # **bold**, lists, `code`, code blocks... in the chat window
    css_file: str = ""  # path to user CSS file (e.g. ~/.config/gtk-4.0/noctalia.css)

    # history / sessions
    history_enabled: bool = True
    history_dir: str = ""  # empty -> ~/.config/ordis/history/

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


def check_base_url(raw: str):
    """Normalise a base URL. Returns (url or None, notes). None means unusable; notes say why."""
    url = (raw or "").strip().rstrip("/")
    notes = []
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            notes.append(f"base_url should stop at the API root; removed trailing {suffix}")
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError as e:
        return None, [f"not a valid URL ({e})"]
    if parts.scheme not in ("http", "https") or not host:
        return None, ["must look like https://host/v1"]
    if parts.query or parts.fragment:
        return None, ["must not contain ? or #"]
    if parts.scheme == "http" and not is_local_host(host):
        notes.append("base_url uses plain http to a non-local host; your API key is sent unencrypted")
    return url, notes


def is_local_host(host) -> bool:
    return host in ("localhost", "127.0.0.1", "::1") or (host or "").endswith(".localhost")


def requires_api_key(cfg: "Config") -> bool:
    """Local servers (ollama, llama.cpp, vLLM on localhost) usually need no key."""
    return not is_local_host(urlsplit(cfg.base_url).hostname)


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
    cfg.base_url = os.environ.get("ORDIS_BASE_URL") or os.environ.get("NVIDIA_BASE_URL") or cfg.base_url
    cfg.model = os.environ.get("ORDIS_MODEL") or os.environ.get("NVIDIA_MODEL", cfg.model)
    fallback = os.environ.get("ORDIS_FALLBACK_MODELS") or os.environ.get("NVIDIA_FALLBACK_MODELS")
    if fallback is not None:
        cfg.fallback_models = [m.strip() for m in fallback.split(",") if m.strip()]
    skill_file = os.environ.get("ORDIS_SKILL_FILE")
    if skill_file is not None:
        cfg.skill_file = skill_file
    css_file = os.environ.get("ORDIS_CSS_FILE")
    if css_file is not None:
        cfg.css_file = css_file
    socket_path = os.environ.get("ORDIS_SOCKET_FILE")
    if socket_path is not None:
        cfg.socket_path = socket_path

    url, notes = check_base_url(cfg.base_url)
    if url is None:
        cfg.warnings.append(
            f"base_url {cfg.base_url!r} ignored: {notes[0]}; using {DEFAULT_BASE_URL}")
        cfg.base_url = DEFAULT_BASE_URL
    else:
        cfg.base_url = url
        cfg.warnings.extend(notes)
    return cfg


def load_api_key(cfg: Config):
    key = os.environ.get("ORDIS_API_KEY") or os.environ.get("NVIDIA_API_KEY")
    if key and key.strip():
        return key.strip()
    path = pathlib.Path(cfg.api_key_file).expanduser() if cfg.api_key_file else config_dir() / "api_key"
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def fetch_models(base_url: str, key=None, timeout: float = 10.0):
    """Query {base_url}/models and return a list of model ids. Empty list on any failure."""
    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url, headers=headers)
        if r.status_code != 200:
            return []
        data = r.json()
        models = []
        for item in data.get("data", []) or []:
            mid = item.get("id") if isinstance(item, dict) else None
            if mid:
                models.append(mid)
        return models
    except Exception:  # noqa: BLE001
        return []


def describe(cfg: Config, key) -> str:
    """Effective settings as text, for `--print-config`. Never includes the key itself."""
    lines = []
    for f in fields(Config):
        if f.name == "warnings":
            continue
        value = getattr(cfg, f.name)
        if f.name in ("system_prompt", "extra_system_prompt") and value:
            value = f"<{len(value)} chars>"
        lines.append(f"{f.name} = {value!r}")
    need = "required" if requires_api_key(cfg) else "not required (local server)"
    lines.append(f"api key: {'set' if key else 'MISSING'}; {need}")
    lines.append(f"config file: {config_dir() / 'config.toml'}"
                 f"{'' if (config_dir() / 'config.toml').exists() else ' (does not exist)'}")
    lines.extend(f"warning: {w}" for w in cfg.warnings)
    return "\n".join(lines)
