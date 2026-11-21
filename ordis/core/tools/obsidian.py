"""Obsidian CLI tool: interact with a running Obsidian vault via obsidian-cli."""

from __future__ import annotations

import shutil

from .env import _run

_WRITE_COMMANDS = {
    "append", "base:create", "bookmark", "create", "delete", "move",
    "plugin:disable", "plugin:enable", "theme:set", "theme:uninstall",
    "template:insert", "history:restore", "sync:restore",
    "dev:cdp", "eval",
}

_REQUIRED_ARGS = {
    "append": ("content",),
    "base:create": ("name", "content"),
    "bookmark": ("url",),
    "create": ("name", "content"),
    "delete": ("file", "path"),
    "dev:cdp": ("method",),
    "eval": ("code",),
    "move": ("file", "path"),
    "theme:set": ("name",),
    "theme:uninstall": ("name",),
}

_JSON_COMMANDS = {
    "aliases", "backlinks", "bases", "base:query", "base:views", "files",
    "links", "outline", "tags", "tasks", "themes", "unresolved", "vault",
    "vaults", "wordcount",
}

_ARG_ALIASES = {
    "create": {
        "file": "name",
        "folder": "path",
    },
    "append": {
        "folder": "path",
    },
    "delete": {
        "file": "file",
        "path": "path",
    },
    "move": {
        "file": "file",
        "path": "path",
    },
    "backlinks": {
        "file": "file",
        "path": "path",
    },
    "outline": {
        "file": "file",
        "path": "path",
    },
    "tags": {
        "file": "file",
        "path": "path",
    },
    "tasks": {
        "file": "file",
        "path": "path",
    },
    "wordcount": {
        "file": "file",
        "path": "path",
    },
    "dev:cdp": {
        "method": "method",
    },
    "eval": {
        "code": "code",
    },
}


def _is_write(command: str) -> bool:
    return any(command == w or command.startswith(w + ":") for w in _WRITE_COMMANDS)


def _normalize_args(command: str, args: dict | None) -> dict:
    if not args:
        return {}
    aliases = _ARG_ALIASES.get(command, {})
    result = {}
    for key, value in args.items():
        result[aliases.get(key, key)] = value
    return result


def _missing_required(command: str, args: dict | None) -> str | None:
    required = _REQUIRED_ARGS.get(command)
    if not required:
        return None
    args = args or {}
    alias_to_canonical = {v: k for k, v in _ARG_ALIASES.get(command, {}).items()}
    missing = [r for r in required if r not in args and alias_to_canonical.get(r) not in args]
    if missing:
        return f"missing required argument(s) for {command}: {', '.join(missing)}"
    return None


def obsidian(self, command, args=None, vault=None, **kwargs):
    if not shutil.which("obsidian-cli"):
        return "error: obsidian-cli is not installed. Install it and make sure it is on PATH."

    if kwargs:
        args = dict(args or {})
        for key, value in kwargs.items():
            args.setdefault(key, value)

    missing = _missing_required(command, args)
    if missing:
        return f"error: {missing}"

    if command in _JSON_COMMANDS and "format" not in (args or {}):
        args = dict(args or {})
        args["format"] = "json"

    cmd = ["obsidian-cli"]
    if vault:
        cmd.append(f"vault={vault}")
    cmd.append(command)
    if args:
        for key, value in _normalize_args(command, args).items():
            cmd.append(f"{key}={value}")

    if _is_write(command) and self.confirm is not None:
        display = " ".join(cmd)
        if not self.confirm(f"Run obsidian-cli command?\n{display}"):
            return "denied: the user did not approve, so the obsidian-cli command was NOT run."

    result = _run(cmd, timeout=self.command_timeout)
    if result.startswith("error:") or result.startswith("Error:"):
        return f"obsidian-cli {command} failed with args {args or {}}: {result}"
    return result
