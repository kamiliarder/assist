"""Window management tools."""
import json

from .env import _run


def list_windows(self):
    wins = json.loads(_run(["niri", "msg", "--json", "windows"]))
    return json.dumps(
        [{"id": w["id"], "title": w.get("title"), "app_id": w.get("app_id")} for w in wins])


def focus_window(self, id):
    return _run(["niri", "msg", "action", "focus-window", "--id", str(int(id))]) or "ok"


def close_window(self, id):
    return _run(["niri", "msg", "action", "close-window", "--id", str(int(id))]) or "ok"


def focus_workspace_down(self):
    """Focus the workspace below (niri: FocusWorkspaceDown)."""
    return _run(["niri", "msg", "action", "focus-workspace-down"]) or "ok"


def focus_workspace_up(self):
    """Focus the workspace above (niri: FocusWorkspaceUp)."""
    return _run(["niri", "msg", "action", "focus-workspace-up"]) or "ok"


def focus_workspace(self, index):
    """Focus a workspace by index (niri: FocusWorkspace)."""
    return _run(["niri", "msg", "action", "focus-workspace", str(int(index))]) or "ok"


def focus_workspace_previous(self):
    """Focus the previous workspace (niri: FocusWorkspacePrevious)."""
    return _run(["niri", "msg", "action", "focus-workspace-previous"]) or "ok"


def center_window(self, id=None):
    """Center a window on the screen (niri: CenterWindow)."""
    cmd = ["niri", "msg", "action", "center-window"]
    if id is not None:
        cmd += ["--id", str(int(id))]
    return _run(cmd) or "ok"


def fullscreen_window(self, id=None):
    """Toggle fullscreen on a window (niri: FullscreenWindow)."""
    cmd = ["niri", "msg", "action", "fullscreen-window"]
    if id is not None:
        cmd += ["--id", str(int(id))]
    return _run(cmd) or "ok"


def switch_preset_window_width(self, id=None):
    """Switch between preset window widths (niri: SwitchPresetWindowWidth)."""
    cmd = ["niri", "msg", "action", "switch-preset-window-width"]
    if id is not None:
        cmd += ["--id", str(int(id))]
    return _run(cmd) or "ok"
