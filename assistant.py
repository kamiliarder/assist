#!/usr/bin/env python3
import os
import pathlib
import sys

# Allow reading the API key from a file (e.g. mounted by sops-nix or agenix).
# ORDIS_API_KEY_FILE takes precedence over NVIDIA_API_KEY_FILE.
key_file = os.environ.get("ORDIS_API_KEY_FILE") or os.environ.get("NVIDIA_API_KEY_FILE")
if key_file:
    try:
        os.environ["ORDIS_API_KEY"] = pathlib.Path(key_file).read_text().strip()
    except OSError:
        pass

if "--print-config" in sys.argv:
    from ordis.core.config import load, describe, load_api_key
    cfg = load()
    print(describe(cfg, load_api_key(cfg)))
    sys.exit(0)

from ordis.ui import create_ui

App = create_ui("gtk")
App().run()
