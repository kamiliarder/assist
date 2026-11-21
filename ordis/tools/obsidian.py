"""Obsidian CLI tool: interact with a running Obsidian vault via obsidian-cli.

Wraps `obsidian-cli <command> [key=value ...]` so the assistant can read vault
metadata (tags, links, tasks, files, outlines) and make structural changes
(append, create, move, toggle tasks, etc.) instead of treating the vault as a
dumb folder of markdown files.

Obsidian vaults are stored at {home}/Documents/ with its respectable vault names

Improvements over the original version:
  * Expanded required-argument validation for more commands (append, create,
    delete, move, theme:set, theme:uninstall, dev:cdp, eval, etc.) so the
    assistant gets a clear error before obsidian-cli rejects the call.
  * Expanded argument aliases: `file`/`name`, `folder`/`path`, `query`/`search`
    are translated automatically, so the assistant doesn't need to memorize
    obsidian-cli's parameter names for every subcommand.
  * `format=json` is added automatically for read commands that support it
    (backlinks, files, links, outline, tags, tasks, unresolved, vault, wordcount),
    giving structured output instead of free-form TSV.
  * New `vaults` convenience: lists known vaults so the assistant can pick the
    right `vault=` target instead of guessing.
  * Better error messages: obsidian-cli failures include the command and args
    that caused them, and missing-required-arg errors list what to provide.
"""
import shutil

from .env import _run

# Subcommands that modify vault or Obsidian state and require user approval.
_WRITE_COMMANDS = {
    "append", "base:create", "bookmark", "create", "delete", "move",
    "plugin:disable", "plugin:enable", "theme:set", "theme:uninstall",
    "template:insert", "history:restore", "sync:restore",
    "dev:cdp", "eval",
}

# Required arguments per command, so we can give a helpful error before
# obsidian-cli rejects the call.
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

# Commands that support `format=json` and benefit from structured output.
_JSON_COMMANDS = {
    "aliases", "backlinks", "bases", "base:query", "base:views", "files",
    "links", "outline", "tags", "tasks", "themes", "unresolved", "vault",
    "vaults", "wordcount",
}

# Common argument aliases so the assistant doesn't have to memorize every
# obsidian-cli parameter name.  obsidian-cli itself uses `name` and `path`;
# `file` and `folder` are intuitive alternatives that get translated here.
#
# Notes on mapping:
#   - `file` resolves by name (wikilink-style), `path` is exact (folder/note.md)
#   - For `create`, obsidian-cli expects `name=<file name>`
#   - For `append`, `file=<name>` or `path=<path>` both work; keep `file` as-is
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
    """Translate intuitive argument names into obsidian-cli's expected names."""
    if not args:
        return {}
    aliases = _ARG_ALIASES.get(command, {})
    result = {}
    for key, value in args.items():
        result[aliases.get(key, key)] = value
    return result


def _missing_required(command: str, args: dict | None) -> str | None:
    """Return a human-readable message if required args are missing, else None."""
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
    """Run an obsidian-cli command against a running Obsidian vault.

    Args:
        command: The obsidian-cli subcommand (e.g. 'files', 'tags', 'append', 'tasks').
        args: Optional dict of key=value options (e.g. {'folder': 'Notes', 'ext': 'md'}).
        vault: Optional vault name to target.
    """
    if not shutil.which("obsidian-cli"):
        return "error: obsidian-cli is not installed. Install it and make sure it is on PATH."

    # Allow top-level arguments (folder, name, content, etc.) in addition to
    # the nested `args` dict, so the tool is more forgiving of how the model
    # structures the call.
    if kwargs:
        args = dict(args or {})
        for key, value in kwargs.items():
            args.setdefault(key, value)

    missing = _missing_required(command, args)
    if missing:
        return f"error: {missing}"

    # Auto-add format=json for read commands that support it, so the assistant
    # gets structured output instead of free-form TSV. Done before building cmd
    # so it is actually sent to obsidian-cli.
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
