"""Shared environment constants and helpers."""
import os
import pathlib
import subprocess

HOME = str(pathlib.Path.home())

# Variables from the nix dev shell that must not leak into apps we launch.
LEAKY_ENV = ("LD_PRELOAD", "GI_TYPELIB_PATH", "GTK4_LAYER_SHELL_LIB", "PYTHONPATH", "PYTHONHOME")


def clean_env():
    return {k: v for k, v in os.environ.items() if k not in LEAKY_ENV}


def _run(cmd, timeout=5):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=clean_env())
    return (r.stdout + r.stderr).strip()
