#!/usr/bin/env python3
import sys

if "--print-config" in sys.argv:
    from ordis.core.config import load, describe, load_api_key
    cfg = load()
    print(describe(cfg, load_api_key(cfg)))
    sys.exit(0)

from ordis.ui import create_ui

App = create_ui("gtk")
App().run()
