# desk-ai

Chat assistant window for niri, talking to NVIDIA NIM
(`https://integrate.api.nvidia.com/v1`, OpenAI-compatible, with tool calling).

A normal GTK4 window: niri can focus other windows while it stays open. Closing it hides it;
running the program again brings it back.

## Layout

```
desk_ai/
  config.py   defaults < ~/.config/desk-ai/config.toml < environment
  llm.py      streaming client, retry/fallback, agent loop   (no GTK)
  tools.py    the tools the model can call, with approvals
  ui.py       the chat window
  app.py      Gtk.Application + main()
assistant.py  thin shim so existing keybinds keep working
tests/        python -m unittest discover -s tests
```

## Run

```sh
git init && git add -A        # flakes only see tracked files
direnv allow                  # or: nix develop

mkdir -p ~/.config/desk-ai && install -m 600 /dev/null ~/.config/desk-ai/api_key
$EDITOR ~/.config/desk-ai/api_key   # paste nvapi-... key

cp config.example.toml ~/.config/desk-ai/config.toml   # optional

python -m desk_ai             # or: python assistant.py
python -m unittest discover -s tests
```

## Config

See `config.example.toml`. Bad or unknown settings are reported in the window and ignored, so a typo
never stops it starting. Environment variables override the file: `NVIDIA_API_KEY`, `NVIDIA_MODEL`,
`NVIDIA_BASE_URL`, `NVIDIA_FALLBACK_MODELS`. The API key itself never goes in `config.toml`.

## niri keybind and window rule (Mod+A is free in your config)

```kdl
Mod+A hotkey-overlay-title="AI Assistant" { spawn "nix" "develop" "/home/qmil/desk-ai" "-c" "python" "-m" "desk_ai"; }
```

Run it from the project directory, or `cd` first, so `python -m desk_ai` finds the package;
`python /home/qmil/desk-ai/assistant.py` works from anywhere and is the safer bind:

```kdl
Mod+A hotkey-overlay-title="AI Assistant" { spawn "nix" "develop" "/home/qmil/desk-ai" "-c" "python" "/home/qmil/desk-ai/assistant.py"; }
```

Optional: open it floating in the bottom-right instead of as a tiled column.

```kdl
window-rule {
    match app-id="^dev\\.local\\.DeskAI$"
    open-floating true
    default-floating-position x=16 y=16 relative-to="bottom-right"
}
```

## Tools

| Tool | Approval |
|---|---|
| `list_windows`, `focus_window` | none |
| `open_app` | none (launches installed .desktop apps only) |
| `media` | none |
| `run_command` | Allow/Deny bar in the window, every time |

Transient provider errors (429/500/502/503/504, network errors) are retried with backoff, then
`fallback_models` are tried in order.
