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

if "--ctl" in sys.argv:
    # ordis --ctl status|cancel|kill|reload|shutdown  (talks to the running ordis-core)
    import json
    from ordis.core.config import load
    from ordis.ipc.client import oneshot
    from ordis.ipc.protocol import resolve_socket_path
    rest = sys.argv[sys.argv.index("--ctl") + 1:]
    action = rest[0] if rest else "status"
    reply = oneshot(resolve_socket_path(load().socket_path), action)
    print(json.dumps(reply, indent=2))
    sys.exit(1 if "error" in reply else 0)

from ordis.ui import create_ui

App = create_ui("gtk")
App().run()
