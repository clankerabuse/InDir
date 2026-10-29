# InDir

A right-click **AI Assistant** for **Dolphin** (KDE Plasma), **Thunar** (Xfce), and **Nautilus** (GNOME Files) on Arch Linux. Right-click a folder or a single file, type a natural-language request, and let the AI inspect and run shell commands there. A file opens focused on that file alone; ask in the chat to include the rest of the folder when you want a wider scope.

## Quick install

```bash
git clone <repo-url> indir
cd indir
./install
```

That's it. The installer walks you through each step and **asks before making changes** (venv, starter config, file permissions, Dolphin/Thunar/Nautilus menus, etc.). Backend, model, and API keys are configured afterward in the **settings UI** (gear icon).

**Non-interactive install:**

```bash
./install -y
```

## Features

- **File manager integration** — "InDir" appears in the right-click menu for files and folders in Dolphin (Plasma 6), Thunar, and Nautilus/Files (GNOME)
- **Persistent chat window** — multi-turn conversation scoped to the selected folder, or to one file when you open a file
- **Configurable backends** — Ollama (local), OpenAI-compatible APIs, Grok (xAI), Anthropic, OpenCode (Zen/Go), optional Cursor SDK — set via the gear icon
- **Safe command execution** — confirm-before-run by default; optional auto-run mode
- **Non-blocking UI** — long commands like ffmpeg run in the background
- **Terminal use** — `indir <request>` runs one request in the current directory and prints the result, with a live one-line indicator while the model or a command works; optional Textual TUI via `--tui` or `ui.mode = "tui"` in config

## Requirements

- Python 3.11+
- Dolphin (KDE Plasma 6), Thunar, and/or Nautilus
- For Nautilus, the `nautilus-python` package (the extension is plain Python; Nautilus loads it in-process)
- One AI backend (configure in settings after install)

Optional Arch packages:

```bash
sudo pacman -S python ollama          # ollama only if using local models
sudo pacman -S nautilus-python        # only for the Nautilus/Files menu
```

## Usage

### From Dolphin, Thunar, or Nautilus

Right-click a folder or a file → **InDir** → chat window opens.

In Nautilus/Files the entry also appears when you right-click the empty background of a folder (that opens InDir on the current folder). Nautilus offers InDir for a single selected item only, because a chat session is scoped to one folder or file.

Opening a **file** keeps the chat on that file. Ask to include the folder (or the other files) and InDir widens the scope to the parent directory.

First run: click the **gear** icon and choose your backend, model, and API key.

Example prompts:

- "convert webm to mp4"
- "list all video files"
- "rename every .jpeg to .jpg"

### From terminal

Ask for something in the current directory — no window, straight to the prompt:

```bash
cd ~/Downloads
indir convert the mp4s to webm
indir rename every .jpeg to .jpg
indir --yes list every video file      # approve commands up front
```

InDir runs the same agent as the chat window: it sees the directory, proposes a
command, and asks before running it:

```
I'll run: ffmpeg -y -i "clip.mp4" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "clip.webm"

Proposed command:
  ffmpeg -y -i "clip.mp4" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "clip.webm"
Run it? [y]es / [N]o / [a]lways:
```

While the model or a command is working, a single line shows what it is
waiting on — `⠹ thinking…`, then `⠼ running ffmpeg … 12s` once a command starts
(it appears after two seconds, so quick turns stay quiet). It is drawn on the
terminal only and erased before any real output, so piped runs and copied text
contain no escape codes. `INDIR_NO_SPINNER=1` turns it off.

`y` runs it, `n` sends the refusal back to the model, `a` runs it and stops
asking for the rest of that run. With no terminal on stdin (a pipe, cron, a
script) nothing is executed unless you pass `--yes`; the blocklist still
applies either way. Exit status is 0 when everything succeeded, 1 when a command
failed or was refused.

Other folders, and the interactive windows:

```bash
indir --prompt 'what is in here?' ~/Downloads   # request against another folder
indir .                                        # open the window here
indir ~/Downloads                              # window for a folder
indir ~/Downloads/clip.webm                    # window focused on one file
indir --tui "list videos"                      # terminal UI, request pre-filled
indir --doctor                                 # check installation
```

A first argument that names an existing file or folder (or a `file://` URI) is
always treated as a target to open; everything else is read as a request for the
current directory. Use `--prompt`/`-p` when a request could look like a path.

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

Using **OpenCode Zen/Go**? Set `provider = "opencode"` and paste your key from
[opencode.ai/auth](https://opencode.ai/auth) — the settings dialog auto-detects
whether your key is a Go subscription or Zen pay-per-use and loads the matching
model list. The gateway serves its whole catalog (Kimi, GLM, DeepSeek, MiniMax,
Claude, GPT, ...) over the OpenAI-compatible endpoint.

See [config.example.toml](config.example.toml) for all options.

## Troubleshooting

```bash
indir --doctor    # or: make doctor
./install --reconfigure  # reset backend / API key
```

| Problem | Fix |
|---------|-----|
| "Could not find indir" in Dolphin/Thunar/Files | Re-run `./install` (uses full path in menu) |
| InDir missing in Files (Nautilus) | Install `nautilus-python`, then `nautilus -q`; re-run `./install` if you skipped the Nautilus step |
| Backend error / connection refused | Open settings (gear) and check provider; for Ollama run `ollama serve` |
| API key missing from file manager | Open settings (gear) and save the key, or set it in config |
| Menu not showing | Restart Dolphin, Thunar (`thunar -q`), or Files (`nautilus -q`), or run `./install` again |

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
