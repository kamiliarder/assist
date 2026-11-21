"""System prompt: built-in default, config overrides, and {placeholders}. No GTK in here."""
import datetime
import pathlib

from .config import Config, config_dir

# The project directory (where ordis/ lives), so the model can find its own code and config.
APP_DIR = pathlib.Path(__file__).resolve().parent.parent

# Cache for skill_file content: {path: (mtime, content)}
_skill_cache: dict[str, tuple[float, str]] = {}


def _read_skill_file(path: pathlib.Path) -> tuple[str | None, str | None]:
    """Read skill file with mtime-based caching. Returns (content, warning)."""
    try:
        stat = path.stat()
        mtime = stat.st_mtime
        cached = _skill_cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1], None
        text = path.read_text(encoding="utf-8")
        _skill_cache[str(path)] = (mtime, text)
        return text, None
    except OSError as e:
        return None, f"skill_file {path}: {e.strerror or e}"


def clear_skill_cache() -> None:
    """Clear the skill file cache. Call on config reload."""
    _skill_cache.clear()

DEFAULT_SYSTEM = """\
You are a concise desktop assistant on a NixOS machine running the niri Wayland compositor.
Today is {date}. The user's home directory is {home}.
You can act through these tools: {tools}. Never guess other tool names.

Rules:
- To launch a GUI app, use open_app.
- To run a command the user asks for, use run_command with exactly that command.
- To change an existing file, read_file it first, then edit_file with the exact snippet. \
Use write_file only for new files or tiny rewrites.
- If no tool fits the request, say plainly what you can't do and suggest the closest option.
- After a tool returns, report its real result. If it says error or denied, say so; \
never claim success you didn't see.
- Keep replies short.
- Installing packages for python is through the flake.nix file at py =

Your own source code is in this directory: {app_dir}.
Respond in a markdown format.
"""

PLACEHOLDERS = ("tools", "date", "home", "app_dir", "config_file", "model")


def render(template, cfg: Config, tool_names, now=None):
    """Fill in {placeholders}. Anything else in braces (JSON examples, etc.) is left alone."""
    now = now or datetime.datetime.now()
    values = {
        "tools": ", ".join(tool_names),
        "date": now.strftime("%A %d %B %Y"),
        "home": str(pathlib.Path.home()),
        "app_dir": str(APP_DIR),
        "config_file": str(config_dir() / "config.toml"),
        "model": cfg.model,
    }
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


def build(cfg: Config, tool_names, now=None):
    """Returns (system_prompt_text, warning_or_None). Reads system_prompt_file every call; skill_file is cached."""
    template, warning = DEFAULT_SYSTEM, None
    if cfg.system_prompt.strip():
        template = cfg.system_prompt
    if cfg.system_prompt_file:
        path = pathlib.Path(cfg.system_prompt_file).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            warning = f"system_prompt_file {path}: {e.strerror or e}; using the configured/default prompt"
        else:
            if text.strip():
                template = text
            else:
                warning = f"system_prompt_file {path} is empty; using the configured/default prompt"
    if cfg.extra_system_prompt.strip():
        template = f"{template.rstrip()}\n\n{cfg.extra_system_prompt.strip()}\n"
    if cfg.skill_file:
        path = pathlib.Path(cfg.skill_file).expanduser()
        text, err = _read_skill_file(path)
        if err:
            if warning:
                warning += f"; {err}"
            else:
                warning = err
        elif text and text.strip():
            template = f"{template.rstrip()}\n\n{text.strip()}\n"
        else:
            if warning:
                warning += f"; skill_file {path} is empty"
            else:
                warning = f"skill_file {path} is empty"
    return render(template, cfg, tool_names, now), warning
