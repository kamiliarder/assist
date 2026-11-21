"""System prompt: built-in default, config overrides, and {placeholders}. No UI dependencies."""

from __future__ import annotations

import datetime
import pathlib

from .config import Config, config_dir


APP_DIR = pathlib.Path(__file__).resolve().parent.parent

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
    return render(template, cfg, tool_names, now), warning
