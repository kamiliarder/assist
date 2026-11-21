"""Shared helpers and the Toolbox core (registration + dispatch)."""
import pathlib

from . import apps, command, fileops, media, obsidian, web, windows
from .env import _run

from .env import LEAKY_ENV  # noqa: F401  (re-exported for backwards compatibility)


def _tool(name, desc, props=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


class Toolbox:
    """`confirm(text) -> bool` is called from a worker thread and may block until answered."""

    def __init__(self, confirm, command_timeout=30):
        self.confirm = confirm
        self.command_timeout = command_timeout
        self._impls = {}
        self.schemas = []
        roots = [pathlib.Path.home()]
        self.fileops = fileops.FileOps(roots=roots, confirm=self.confirm)
        self._bind_methods()
        self._register()

    def _register(self):
        self._impls.update({
            "list_windows": self.list_windows,
            "focus_window": self.focus_window,
            "focus_workspace_down": self.focus_workspace_down,
            "focus_workspace_up": self.focus_workspace_up,
            "focus_workspace": self.focus_workspace,
            "focus_workspace_previous": self.focus_workspace_previous,
            "center_window": self.center_window,
            "fullscreen_window": self.fullscreen_window,
            "switch_preset_window_width": self.switch_preset_window_width,
            "open_app": self.open_app,
            "media": self.media,
            "run_command": self.run_command,
            "read_file": self.read_file,
            "edit_file": self.edit_file,
            "write_file": self.write_file,
            "web_open": self.web_open,
            "web_search": self.web_search,
            "web_read": self.web_read,
            "web_tavily_search": self.web_tavily_search,
            "web_tavily_extract": self.web_tavily_extract,
            "web_tavily_search_and_extract": self.web_tavily_search_and_extract,
            "obsidian": self.obsidian,
        })
        self.schemas.extend([
            _tool("list_windows", "List open windows (id, title, app_id)."),
            _tool("focus_window", "Focus a window by id from list_windows.",
                  {"id": {"type": "integer"}}, ["id"]),
            _tool("focus_workspace_down", "Focus the workspace below (niri: FocusWorkspaceDown)."),
            _tool("focus_workspace_up", "Focus the workspace above (niri: FocusWorkspaceUp)."),
            _tool("focus_workspace", "Focus a workspace by index (niri: FocusWorkspace).",
                  {"index": {"type": "integer"}}, ["index"]),
            _tool("focus_workspace_previous", "Focus the previous workspace (niri: FocusWorkspacePrevious)."),
            _tool("center_window", "Center a window on the screen (niri: CenterWindow).",
                  {"id": {"type": "integer"}}),
            _tool("fullscreen_window", "Toggle fullscreen on a window (niri: FullscreenWindow).",
                  {"id": {"type": "integer"}}),
            _tool("switch_preset_window_width", "Switch between preset window widths (niri: SwitchPresetWindowWidth).",
                  {"id": {"type": "integer"}}),
            _tool("open_app", "Launch an installed GUI application by its display name, e.g. 'Firefox'.",
                  {"name": {"type": "string"}}, ["name"]),
            _tool("media", "Control the active MPRIS media player.",
                  {"action": {"type": "string",
                              "enum": ["play-pause", "next", "previous", "status", "now-playing"]}},
                  ["action"]),
            _tool("run_command",
                  "Run a shell command on the user's machine. The user must approve each command. "
                  "Use detach=true for long-running or GUI programs.",
                  {"command": {"type": "string"}, "detach": {"type": "boolean"}}, ["command"]),
            _tool("read_file",
                  "Read part or all of a text file. Returns the requested line range.",
                  {"file_path": {"type": "string"},
                   "start_line": {"type": "integer"},
                   "end_line": {"type": "integer"}}, ["file_path"]),
            _tool("edit_file",
                  "Replace an exact snippet in an existing file. search_block must match the file "
                  "verbatim (indentation, whitespace, line breaks).",
                  {"file_path": {"type": "string"},
                   "search_block": {"type": "string"},
                   "replace_block": {"type": "string"}}, ["file_path", "search_block"]),
            _tool("write_file",
                  "Create a new file, or rewrite an existing small file (< 50 lines). "
                  "For larger existing files use read_file + edit_file instead.",
                  {"file_path": {"type": "string"},
                   "content": {"type": "string"}}, ["file_path", "content"]),
            _tool("web_open", "Open a URL in Firefox.",
                  {"url": {"type": "string"}}, ["url"]),
            _tool("web_search", "Search the web in Firefox.",
                  {"query": {"type": "string"}}, ["query"]),
            _tool("web_read", "Fetch a URL and return its text content.",
                  {"url": {"type": "string"}}, ["url"]),
            _tool("web_tavily_search", "Search the web using Tavily API and return structured results.",
                  {"query": {"type": "string"},
                   "max_results": {"type": "integer"},
                   "search_depth": {"type": "string", "enum": ["basic", "advanced"]},
                   "include_domains": {"type": "array", "items": {"type": "string"}},
                   "exclude_domains": {"type": "array", "items": {"type": "string"}}},
                  ["query"]),
            _tool("web_tavily_extract", "Extract content from URLs using Tavily API.",
                  {"urls": {"type": "array", "items": {"type": "string"}},
                   "include_images": {"type": "boolean"}},
                  ["urls"]),
            _tool("web_tavily_search_and_extract", "Search and extract content in one call using Tavily API.",
                  {"query": {"type": "string"},
                   "max_results": {"type": "integer"},
                   "search_depth": {"type": "string", "enum": ["basic", "advanced"]}},
                  ["query"]),
            _tool("obsidian",
                  "Run an obsidian-cli command against a running Obsidian vault "
                  "(files, tags, tasks, append, move, outline, links, etc.). "
                  "State-changing commands require user approval. "
                  "Common aliases: 'file' -> 'name'/'path', 'folder' -> 'path'.",
                  {"command": {"type": "string"},
                   "args": {"type": "object",
                            "description": "Optional key=value options for the command, e.g. {'folder': 'General', 'name': 'Note.md', 'content': '...'}"},
                   "vault": {"type": "string",
                             "description": "Optional vault name to target"}},
                  ["command"]),
        ])

    def _bind_methods(self):
        for mod in (windows, apps, media, command, obsidian):
            for name in ("list_windows", "focus_window", "open_app", "media", "run_command",
                         "focus_workspace_down", "focus_workspace_up", "focus_workspace",
                         "focus_workspace_previous", "center_window", "fullscreen_window",
                         "switch_preset_window_width", "obsidian"):
                fn = getattr(mod, name, None)
                if fn is not None:
                    setattr(self, name, fn.__get__(self, Toolbox))
        for name in ("read_file", "edit_file", "write_file"):
            fn = getattr(self.fileops, name, None)
            if fn is not None:
                setattr(self, name, fn)
        for name in ("open", "search", "read", "tavily_search", "tavily_extract", "tavily_search_and_extract"):
            fn = getattr(web, name, None)
            if fn is not None:
                setattr(self, "web_" + name, fn.__get__(self, Toolbox))

    def names(self):
        return list(self._impls)

    def call(self, name, args):
        fn = self._impls.get(name)
        if fn is None:
            return f"error: unknown tool '{name}'. Available tools: {', '.join(self._impls)}"
        try:
            return str(fn(**args))
        except TypeError as e:
            return f"error: bad arguments for {name}: {e}"
        except Exception as e:  # noqa: BLE001
            # The model can only fix what it can see (e.g. "search_block appears 3 times").
            return f"error: {e or type(e).__name__}"
