"""Shell command tool."""

import subprocess

from .env import HOME, clean_env


def run_command(self, command, detach=False):
    """Run a shell command, but only after the user approves it in the UI."""
    if not self.confirm(command):
        return "denied: the user did not approve, so the command was NOT run"
    kwargs = dict(env=clean_env(), cwd=HOME)
    if detach:
        subprocess.Popen(
            ["sh", "-c", command], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, **kwargs)
        return "started in the background"
    r = subprocess.run(["sh", "-c", command], capture_output=True, text=True,
                       timeout=self.command_timeout, **kwargs)
    out = (r.stdout + r.stderr).strip()
    return f"exit code {r.returncode}\n{out[-3000:]}"
