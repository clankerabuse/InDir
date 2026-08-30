# InDir

A Dolphin right-click **AI Assistant** for KDE Plasma on Arch Linux. Right-click any directory, type a natural-language request, and let the AI inspect files and run shell commands in that folder.

## Quick install

```bash
git clone <repo-url> indir
cd indir
./install
```

That's it. The installer walks you through each step and **asks before making changes** (venv, config, file permissions, Dolphin menu, etc.).

**Non-interactive install (requires explicit backend and model):**

```bash
./install -y --provider grok --model grok-3
```

**Install with Grok (interactive — prompts for backend, model, and API key):**

```bash
./install --provider grok
```

## Features

- **Dolphin integration** — "InDir" appears in the right-click menu for directories (Plasma 6)
- **Persistent chat window** — multi-turn conversation scoped to the selected directory
- **Configurable backends** — Ollama (local), OpenAI-compatible APIs, Grok (xAI), Anthropic, optional Cursor SDK
- **Safe command execution** — confirm-before-run by default; optional auto-run mode
- **Non-blocking UI** — long commands like ffmpeg run in the background
- **Terminal UI** — optional Textual TUI via `--tui` or `ui.mode = "tui"` in config

## Requirements

- Python 3.11+
- KDE Plasma 6 / Dolphin
- One AI backend (installer helps you set this up)

Optional Arch packages:

```bash
sudo pacman -S python ollama   # ollama only if using local models
```

## Usage

### From Dolphin

Right-click a directory → **InDir** → chat window opens.

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

Config lives at `~/.config/indir/config.toml`. Re-run the wizard anytime:

```bash
./install --reconfigure
```

Cloud API keys can be stored directly in config (recommended for Dolphin):

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
| "Could not find indir" in Dolphin | Re-run `./install` (uses full path in menu) |
| Backend error / connection refused | Check provider in config; for Ollama run `ollama serve` |
| API key missing in Dolphin | Re-run `./install --reconfigure` to save key in config |
| Menu not showing | Restart Dolphin, or run `./install` again |

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
