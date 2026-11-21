#!/usr/bin/env python3
import sys

if "--print-config" in sys.argv:
    from desk_ai.core.config import load, describe, load_api_key
    cfg = load()
    print(describe(cfg, load_api_key(cfg)))
    sys.exit(0)

from desk_ai.ui import create_ui

App = create_ui("gtk")
App().run()
