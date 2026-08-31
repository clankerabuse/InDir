# InDir

A right-click **AI Assistant** for **Dolphin** (KDE Plasma) and **Thunar** (Xfce) on Arch Linux. Right-click any directory, type a natural-language request, and let the AI inspect files and run shell commands in that folder.

## Quick install

```bash
git clone <repo-url> indir
cd indir
./install
```

That's it. The installer walks you through each step and **asks before making changes** (venv, starter config, file permissions, Dolphin/Thunar menus, etc.). Backend, model, and API keys are configured afterward in the **settings UI** (gear icon).

**Non-interactive install:**

```bash
./install -y
```

## Features

- **File manager integration** — "InDir" appears in the right-click menu for directories in Dolphin (Plasma 6) and Thunar
- **Persistent chat window** — multi-turn conversation scoped to the selected directory
- **Configurable backends** — Ollama (local), OpenAI-compatible APIs, Grok (xAI), Anthropic, optional Cursor SDK — set via the gear icon
- **Safe command execution** — confirm-before-run by default; optional auto-run mode
- **Non-blocking UI** — long commands like ffmpeg run in the background
- **Terminal UI** — optional Textual TUI via `--tui` or `ui.mode = "tui"` in config

## Requirements

- Python 3.11+
- Dolphin (KDE Plasma 6) and/or Thunar
- One AI backend (configure in settings after install)

Optional Arch packages:

```bash
sudo pacman -S python ollama   # ollama only if using local models
```

## Usage

### From Dolphin or Thunar

Right-click a directory → **InDir** → chat window opens.

First run: click the **gear** icon and choose your backend, model, and API key.

Example prompts:

- "convert webm to mp4"
- "list all video files"
- "rename every .jpeg to .jpg"

### From terminal

```bash
indir ~/Downloads
indir --tui ~/Downloads
indir --doctor          # check installation
```

## Configuration

Config lives at `~/.config/indir/config.toml`. Prefer the in-app **settings** dialog (gear icon) to pick provider, model, and API key.

To reset to starter defaults:

```bash
./install --reset-config
```

Cloud API keys can also be stored directly in config (useful when launching from a file manager):

```toml
[backend]
provider = "grok"

[backend.grok]
api_key = "xai-your-key-here"
model = "grok-3"
```

Or use environment variables (`XAI_API_KEY`, `OPENAI_API_KEY`, etc.).

See [config.example.toml](config.example.toml) for all options.

## Troubleshooting

```bash
indir --doctor    # or: make doctor
./install --reconfigure  # reset backend / API key
```

| Problem | Fix |
|---------|-----|
| "Could not find indir" in Dolphin/Thunar | Re-run `./install` (uses full path in menu) |
| Backend error / connection refused | Open settings (gear) and check provider; for Ollama run `ollama serve` |
| API key missing from file manager | Open settings (gear) and save the key, or set it in config |
| Menu not showing | Restart Dolphin or Thunar (`thunar -q`), or run `./install` again |

## Uninstall

```bash
./scripts/uninstall.sh
# or: make uninstall
```

## Development

```bash
make test
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT
