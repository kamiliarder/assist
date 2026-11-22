# ordis

> [!WARNING]
> AI Generated Code, PR's and fixes are welcome


Chat assistant window for niri, talking to any OpenAI-compatible provider
(NVIDIA NIM, Ollama, vLLM, llama.cpp, etc.).

ordis runs as two parts: a headless **core daemon** (`ordis-core`) that stays
running in the background, and a **GUI client** (`ordis`) that connects to it
via a Unix socket. This lets you keep the AI session alive while opening and
closing the chat window as needed.

## Architecture

```
ordis-core  (systemd user service, socket-activated)
  owns: the session, history, tools, and the one connection to the model
  listens on $XDG_RUNTIME_DIR/ordis/control.sock   (mode 0600, same-uid only)

ordis       (GTK4 window, launched on demand)
  holds no session: attaches to ordis-core, shows the live stream, sends commands
```

Closing the window ends the GUI process only. A running turn keeps going in the daemon, and the
next window attaches to it (including mid-turn). Stopping the daemon ends the session and its
model connection.

Cancel (and `kill`, the same thing) sets the cancel flag *and* closes the HTTP stream, so the
provider connection really goes away and the agent does not retry it.

Approvals (run_command, file edits, tool-round pauses) are asked of whichever GUI is attached.
With no GUI attached they are denied.

### Control from a shell

```sh
ordis --ctl status      # busy? model? uptime? attached GUIs
ordis --ctl cancel      # stop the running turn and drop the model connection ("kill" is an alias)
ordis --ctl reload      # re-read config.toml
ordis --ctl shutdown    # stop the daemon
```

Protocol: newline-delimited JSON over the socket, see `ordis/ipc/protocol.py`.

## Layout

```
ordis/
  core/         headless daemon logic (config, agent, toolbox, history, prompt, tools, types)
  ipc/          socket protocol, daemon-side server, client library
  ui/           GTK4 client (panel, app, mdview)
  markdown.py   markdown parser
  config.example.toml   example config
  ordis-core    headless daemon entry point
  assistant.py  GUI client entry point
  tests/        unit tests
```

## Configuration

Settings live in `~/.config/ordis/config.toml` or via environment variables
(`ORDIS_*` prefix, legacy `NVIDIA_*` still works as fallback).

```toml
# provider (any OpenAI-compatible /v1 endpoint)
base_url = "https://integrate.api.nvidia.com/v1"
model = "nvidia/nemotron-3.5-super-vl-120b-a12b"
fallback_models = ["meta/llama-3.3-70b-instruct"]
api_key_file = "~/.config/ordis/api_key"

# system prompt
system_prompt_file = "~/.config/ordis/system_prompt.md"
extra_system_prompt = ""
skill_file = "~/.config/ordis/SKILL.md"

# window
window_width = 460
window_height = 600
render_markdown = true
css_file = "~/.config/gtk-4.0/noctalia.css"

# socket: leave unset (default $XDG_RUNTIME_DIR/ordis/control.sock; the NixOS socket unit uses it)
# socket_path = "/some/other/path.sock"
```

Environment variables: `ORDIS_BASE_URL`, `ORDIS_MODEL`, `ORDIS_API_KEY`,
`ORDIS_SKILL_FILE`, `ORDIS_CSS_FILE`, `ORDIS_SOCKET_FILE`.

## Run

### Development

```sh
direnv allow                  # or: nix develop

mkdir -p ~/.config/ordis && install -m 600 /dev/null ~/.config/ordis/api_key
$EDITOR ~/.config/ordis/api_key   # paste nvapi-... key

cp ordis/config.example.toml ~/.config/ordis/config.toml   # optional

# Headless core daemon (first), then the GUI client
python ordis-core
python assistant.py

# Check effective settings
python assistant.py --print-config

# Run tests
python -m unittest discover -s ordis -t .
```

### NixOS

See `my own nixos configuration on ordis.nix` for the NixOS module.
It installs `ordis-core` as a socket-activated systemd user service and `ordis` as a GUI client
available via `Mod+A` in niri. API key is managed via agenix. The daemon's PATH comes from the
module's `extraPath` option, not from your shell.

## SKILL.md

Create `~/.config/ordis/SKILL.md` to add persistent instructions that are
appended to the system prompt on every turn. Edit it live — changes take
effect immediately (cached by mtime, cleared on `/reload`).

## Tools

| Tool | Approval |
|---|---|
| `list_windows`, `focus_window` | none |
| `open_app` | none |
| `media` | none |
| `web_open`, `web_search` | none |
| `read_file` | none |
| `edit_file`, `write_file` | Allow/Deny with a diff |
| `run_command` | Allow/Deny bar in the window |
| `web_read`, `web_tavily_*` | none |
| `obsidian` | Allow/Deny for write commands |

Transient provider errors (429/500/502/503/504, network errors) are retried
with backoff, then `fallback_models` are tried in order.

## Chat commands

- `/reload` — re-read config.toml and clear skill cache
- `/restart` — full process restart
- `/prev-session` — switch to previous chat session
- `/new` — start a new session
