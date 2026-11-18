"""Window management tools."""
import json

from .env import _run


def list_windows(self):
    wins = json.loads(_run(["niri", "msg", "--json", "windows"]))
    return json.dumps(
        [{"id": w["id"], "title": w.get("title"), "app_id": w.get("app_id")} for w in wins])


def focus_window(self, id):
    return _run(["niri", "msg", "action", "focus-window", "--id", str(int(id))]) or "ok"
