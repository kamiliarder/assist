# ordis

> [!WARNING]
> AI Generated Code, PR's and fixes are welcome


Chat assistant window for niri, talking to any OpenAI-compatible provider
(NVIDIA NIM, Ollama, vLLM, llama.cpp, etc.).

A normal GTK4 window: niri can focus other windows while it stays open. Closing it hides it;
running the program again brings it back.

## Layout

```
ordis/
  config.py   defaults < ~/.config/ordis/config.toml < environment; base_url checks
  prompt.py   system prompt: default, overrides, {placeholders}   (no GTK)
  llm.py      streaming client, retry/fallback, agent loop        (no GTK)
  tools/      the tools the model can call (one module per area), with approvals
  ui.py       the chat window
  app.py      Gtk.Application + main()
  test_*.py   tests
assistant.py  thin shim so existing keybinds keep working
```

## Run

```sh
git init && git add -A        # flakes only see tracked files
direnv allow                  # or: nix develop

mkdir -p ~/.config/ordis && install -m 600 /dev/null ~/.config/ordis/api_key
$EDITOR ~/.config/ordis/api_key   # paste nvapi-... key

cp ordis/config.example.toml ~/.config/ordis/config.toml   # optional

python assistant.py                  # or: python -m ordis (from the project directory)
python assistant.py --print-config   # effective settings + the exact system prompt, then exit
python -m unittest discover -s ordis -t .
```

## Tools

| Tool | Approval |
|---|---|
| `list_windows`, `focus_window` | none |
| `open_app` | none (launches installed .desktop apps only) |
| `media` | none |
| `web_open`, `web_search` | none |
| `read_file` | none |
| `edit_file`, `write_file` | Allow/Deny with a diff, every time |
| `run_command` | Allow/Deny bar in the window, every time |

Transient provider errors (429/500/502/503/504, network errors) are retried with backoff, then
`fallback_models` are tried in order.
