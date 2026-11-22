"""File search tool using fd (filenames) and ripgrep (content). Auto-approved; no confirmation required."""

from __future__ import annotations

import pathlib
import shutil
import subprocess


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def find_file(self, pattern: str, path: str | None = None, content: bool = False,
              max_results: int = 100) -> str:
    """Search for files by name (fd) or by content (ripgrep).

    Args:
        pattern: Search pattern (filename glob/regex or content regex).
        path: Directory to search in. Defaults to the home directory.
        content: If True, search file contents with ripgrep; otherwise search filenames with fd.
        max_results: Maximum number of results to return.
    """
    root = pathlib.Path(path or pathlib.Path.home()).expanduser()
    if not root.is_dir():
        return f"error: search path '{root}' is not a directory"

    if content:
        exe = _which("rg")
        if not exe:
            return "error: ripgrep (rg) is not installed"
        try:
            r = subprocess.run(
                [exe, "--no-heading", "--line-number", "--max-count", str(max_results),
                 pattern, str(root)],
                capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return "error: ripgrep search timed out"
        except OSError as e:
            return f"error: ripgrep failed: {e}"
        out = r.stdout.strip()
        if not out:
            return "no matches found"
        return f"--- ripgrep results ---\n{out[-3000:]}"

    exe = _which("fd")
    if not exe:
        return "error: fd-find (fd) is not installed"
    try:
        r = subprocess.run(
            [exe, "--max-results", str(max_results), pattern, str(root)],
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "error: fd search timed out"
    except OSError as e:
        return f"error: fd failed: {e}"
    out = r.stdout.strip()
    if not out:
        return "no matches found"
    return f"--- fd results ---\n{out[-3000:]}"
