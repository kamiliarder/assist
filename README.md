# desk-ai

Chat assistant window for niri, talking to NVIDIA NIM
(`https://integrate.api.nvidia.com/v1`, OpenAI-compatible, with tool calling).

A normal GTK4 window: niri can focus other windows while it stays open. Closing it hides it;
running the program again brings it back.

## Layout

```
desk_ai/
  config.py   defaults < ~/.config/desk-ai/config.toml < environment; base_url checks
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

mkdir -p ~/.config/desk-ai && install -m 600 /dev/null ~/.config/desk-ai/api_key
$EDITOR ~/.config/desk-ai/api_key   # paste nvapi-... key

cp desk_ai/config.example.toml ~/.config/desk-ai/config.toml   # optional

python assistant.py                  # or: python -m desk_ai (from the project directory)
python assistant.py --print-config   # effective settings + the exact system prompt, then exit
python -m unittest discover -s desk_ai -t .
```

## Config

See `desk_ai/config.example.toml`. Bad or unknown settings are reported in the window and ignored, so a
typo never stops it starting. Environment variables override the file: `NVIDIA_API_KEY`,
`NVIDIA_MODEL`, `NVIDIA_BASE_URL`, `NVIDIA_FALLBACK_MODELS`. The API key itself never goes in
`config.toml`. Run `python assistant.py --print-config` to see what actually took effect.

**Base URL.** Any OpenAI-compatible API root works. A trailing `/` or a pasted `/chat/completions` is
cleaned up (with a note); an unusable value falls back to the NVIDIA default with a warning. Plain
`http://` to a non-local host warns that the key travels unencrypted. For a server on `localhost`
(ollama, llama.cpp, vLLM) no API key is needed and none is sent.

**System prompt.** Priority: `system_prompt_file` > `system_prompt` > the built-in default in
`prompt.py`; `extra_system_prompt` is appended to whichever wins. These placeholders are filled in:
`{tools} {date} {home} {app_dir} {config_file} {model}`. Other braces are left alone, so JSON in a
prompt is safe. The file is re-read every turn, so you can tune the prompt without restarting; an
unreadable or empty file warns once in the window and falls back.

## niri keybind and window rule (Mod+A is free in your config)

```kdl
Mod+A hotkey-overlay-title="AI Assistant" { spawn "nix" "develop" "/home/qmil/Projects/assist" "-c" "python" "/home/qmil/Projects/assist/assistant.py"; }
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
| `web_open`, `web_search` | none |
| `read_file` | none |
| `edit_file`, `write_file` | Allow/Deny with a diff, every time |
| `run_command` | Allow/Deny bar in the window, every time |

Transient provider errors (429/500/502/503/504, network errors) are retried with backoff, then
`fallback_models` are tried in order.
