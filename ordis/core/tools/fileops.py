"""File tools: read_file, edit_file, write_file. No UI dependencies."""

from __future__ import annotations

import ast
import difflib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import tomllib

from ..config import config_dir

SENSITIVE_DIRS = {".ssh", ".gnupg", ".aws", ".password-store"}
MAX_DIFF_CHARS = 4000
MAX_READ_CHARS = 20000


class FileError(Exception):
    """A problem the model can fix. The message goes back to it as the tool result."""


# ---------------------------------------------------------------- syntax validation


def _check_with_tool(cmd, text, suffix, marker):
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / f"check{suffix}"
        f.write_text(text, encoding="utf-8")
        try:
            r = subprocess.run([exe, *cmd[1:], str(f)], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if r.returncode == 0:
        return None
    err = (r.stderr or r.stdout).strip().replace(str(f), f"<file{suffix}>")
    if marker and marker not in err.lower():
        return None
    return err[:500]


def validate_syntax(path, text):
    suffix = pathlib.Path(path).suffix.lower()
    try:
        if suffix in (".py", ".pyi"):
            ast.parse(text, filename=pathlib.Path(path).name)
        elif suffix == ".json":
            json.loads(text)
        elif suffix == ".toml":
            tomllib.loads(text)
        elif suffix == ".nix":
            return _check_with_tool(["nix-instantiate", "--parse"], text, ".nix", "syntax error")
        elif suffix in (".sh", ".bash"):
            return _check_with_tool(["bash", "-n"], text, suffix, None)
    except SyntaxError as e:
        where = f"line {e.lineno}" + (f", column {e.offset}" if e.offset else "")
        snippet = f"\n    {e.text.strip()}" if e.text else ""
        return f"SyntaxError: {e.msg} ({where}){snippet}"
    except json.JSONDecodeError as e:
        return f"invalid JSON: {e}"
    except tomllib.TOMLDecodeError as e:
        return f"invalid TOML: {e}"
    except (ValueError, RecursionError) as e:
        return f"could not parse: {e}"
    return None


# ---------------------------------------------------------------- the tools


class FileOps:
    def __init__(self, roots, confirm=None, rewrite_max_lines=50, max_bytes=512_000,
                 readonly_prefixes=("/nix/store/",), protected_paths=None):
        self.roots = [pathlib.Path(r).expanduser().resolve() for r in roots]
        self.confirm = confirm
        self.rewrite_max_lines = rewrite_max_lines
        self.max_bytes = max_bytes
        self.readonly_prefixes = tuple(readonly_prefixes)
        if protected_paths is None:
            protected_paths = [config_dir()]
        self.protected = [pathlib.Path(p).expanduser().resolve() for p in protected_paths]

    def _within_roots(self, p):
        return any(p == r or p.is_relative_to(r) for r in self.roots)

    def _is_readonly(self, p):
        return str(p).startswith(self.readonly_prefixes)

    def _resolve(self, file_path, write=False):
        if not isinstance(file_path, str) or not file_path.strip():
            raise FileError("file_path is required")
        p = pathlib.Path(file_path.strip()).expanduser()
        if not p.is_absolute():
            raise FileError("use an absolute path, or one starting with ~/")
        lexical = pathlib.Path(os.path.abspath(p))
        real = p.resolve()
        if write and self._is_readonly(real):
            raise FileError(
                f"{real} is inside the Nix store, which is read-only and managed by Nix. "
                "If this file comes from home-manager or your NixOS config, edit its source "
                "file in your flake instead.")
        readable_store = (not write) and self._is_readonly(real)
        if not self._within_roots(lexical) or not (self._within_roots(real) or readable_store):
            raise FileError(
                f"{real} is outside the allowed folders ({', '.join(map(str, self.roots))})")
        for q in (lexical, real):
            if SENSITIVE_DIRS & set(q.parts) or any(q == s or q.is_relative_to(s) for s in self.protected):
                raise FileError("access to this path is blocked (it may hold credentials)")
        if write and ".git" in real.parts:
            raise FileError("writing inside a .git directory is blocked")
        return real

    def _load(self, real):
        if not real.is_file():
            raise FileError(f"{real} is not an existing regular file")
        size = real.stat().st_size
        if size > self.max_bytes:
            raise FileError(f"file is too large ({size} bytes; limit {self.max_bytes})")
        data = real.read_bytes()
        if b"\0" in data[:8192]:
            raise FileError("file looks binary; only text files are supported")
        try:
            return data, data.decode("utf-8")
        except UnicodeDecodeError:
            raise FileError("file is not valid UTF-8") from None

    def read_file(self, file_path, start_line=1, end_line=None):
        real = self._resolve(file_path)
        _, text = self._load(real)
        lines = text.splitlines(keepends=True)
        total = len(lines)
        if total == 0:
            return f"--- {file_path} (empty file) ---\n"
        start = max(1, int(start_line or 1))
        if start > total:
            raise FileError(f"{file_path} has only {total} lines")
        end = min(total, int(end_line)) if end_line else total
        if end < start:
            raise FileError("end_line is before start_line")
        chunk = "".join(lines[start - 1:end])
        note = ""
        if len(chunk) > MAX_READ_CHARS:
            chunk = chunk[:MAX_READ_CHARS]
            chunk = chunk[:chunk.rfind("\n") + 1] or chunk
            end = start + chunk.count("\n") - 1
            note = f"\n[output truncated; call read_file again with start_line={end + 1}]"
        return f"--- {file_path} lines {start}-{end} of {total} ---\n{chunk}{note}"

    @staticmethod
    def _find_all(text, needle):
        hits, i = [], text.find(needle)
        while i != -1:
            hits.append(i)
            i = text.find(needle, i + 1)
        return hits

    @staticmethod
    def _not_found_message(text, search):
        msg = ("search_block was not found. It must match the file EXACTLY, including indentation, "
                "whitespace and line breaks. Call read_file again and copy the snippet verbatim.")
        norm = lambda s: re.sub(r"\s+", " ", s).strip()
        if norm(search) and norm(search) in norm(text):
            return msg + " It DOES match if whitespace is ignored, so the difference is indentation, tabs or line breaks."
        first = next((ln.strip() for ln in search.splitlines() if ln.strip()), "")
        if first:
            at = [i + 1 for i, ln in enumerate(text.splitlines()) if first in ln]
            if at:
                return msg + f" The first line of your block appears (ignoring indentation) at line(s) {at[:5]}; compare from there."
            return msg + " The first line of your block does not appear anywhere in the file."
        return msg

    def edit_file(self, file_path, search_block, replace_block):
        if not search_block:
            raise FileError("search_block is empty. Provide the exact existing snippet to replace.")
        if replace_block is None:
            replace_block = ""
        if search_block == replace_block:
            raise FileError("search_block and replace_block are identical; there is nothing to change.")
        real = self._resolve(file_path, write=True)
        if not real.exists():
            raise FileError(f"{file_path} does not exist. Use write_file to create new files.")
        data, old = self._load(real)

        search, replace = search_block, replace_block
        if "\r\n" in old and "\r\n" not in search:
            search, replace = search.replace("\n", "\r\n"), replace.replace("\n", "\r\n")

        hits = self._find_all(old, search)
        if not hits:
            raise FileError(self._not_found_message(old, search_block))
        if len(hits) > 1:
            where = [old.count("\n", 0, i) + 1 for i in hits]
            raise FileError(
                f"search_block appears {len(hits)} times (starting at lines {where[:8]}). "
                "Include a few more surrounding lines so it matches exactly once.")
        i = hits[0]
        new = old[:i] + replace + old[i + len(search):]

        if not self._commit(real, old, new, data, file_path, "Edit this file?"):
            return "denied: the user did not approve; the file was NOT changed."
        line = old.count("\n", 0, i) + 1
        return (f"edited {file_path}: replaced {len(search_block.splitlines())} line(s) with "
                f"{len(replace_block.splitlines())} line(s) at line {line}")

    def write_file(self, file_path, content):
        if not isinstance(content, str):
            raise FileError("content must be a string")
        real = self._resolve(file_path, write=True)
        if len(content.encode("utf-8")) > self.max_bytes:
            raise FileError(f"content is too large (limit {self.max_bytes} bytes)")
        n_new = len(content.splitlines())

        if real.exists():
            data, old = self._load(real)
            n_old = len(old.splitlines())
            if max(n_old, n_new) >= self.rewrite_max_lines:
                raise FileError(
                    f"{file_path} already exists ({n_old} lines; new content {n_new} lines). "
                    f"write_file only rewrites existing files under {self.rewrite_max_lines} lines. "
                    "Use read_file and edit_file to make targeted changes.")
            if content == old:
                raise FileError("content is identical to the existing file; nothing to change.")
            if not self._commit(real, old, content, data, file_path, "Overwrite this small file?"):
                return "denied: the user did not approve; the file was NOT changed."
            return f"rewrote {file_path} ({n_new} lines)"

        if not self._commit(real, "", content, None, file_path, "Create this new file?"):
            return "denied: the user did not approve; no file was created."
        return f"created {file_path} ({n_new} lines)"

    def _commit(self, real, old, new, original_bytes, display, title):
        err = validate_syntax(real, new)
        if err:
            raise FileError(
                "rejected: the result would have a syntax error, so NOTHING was written.\n"
                f"{err}\nFix the code in your replacement and try again.")
        if self.confirm is not None:
            if not self.confirm(f"{title}\n{display}\n\n{self._diff(old, new, display)}"):
                return False
            if original_bytes is None:
                if real.exists():
                    raise FileError("the file was created by something else while waiting; read it and retry")
            elif not real.exists() or real.read_bytes() != original_bytes:
                raise FileError("the file changed while waiting for approval; call read_file again and retry")
        self._atomic_write(real, new)
        return True

    @staticmethod
    def _diff(old, new, name):
        d = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                         f"a/{name}", f"b/{name}", n=2))
        if len(d) > MAX_DIFF_CHARS:
            d = d[:MAX_DIFF_CHARS].rsplit("\n", 1)[0] + "\n… (diff truncated)"
        return d or "(no textual change)"

    @staticmethod
    def _atomic_write(real, text):
        real.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            if real.exists():
                shutil.copymode(real, tmp)
            else:
                os.chmod(tmp, 0o644)
            os.replace(tmp, real)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
