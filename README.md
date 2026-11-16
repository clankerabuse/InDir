# InDir

Right-click **AI assistant** for Dolphin (Plasma 6), Thunar and Nautilus on Arch
Linux. Right-click a folder or a file, type a natural-language request, and let
the AI inspect and run shell commands there.

## Install

```bash
git clone https://github.com/clankerabuse/InDir.git indir
cd indir
./install          # add -y for non-interactive
```

The installer asks before each change (venv, config, file permissions, file
manager menus). Pick the backend, model and API key in the **settings UI** (gear
icon) afterwards.

Needs Python 3.11+ and one backend: Ollama, an OpenAI-compatible API, Grok,
Anthropic or OpenCode Zen/Go. Nautilus also needs `nautilus-python`:

```bash
sudo pacman -S nautilus-python && nautilus -q
```

## Use

Right-click a folder or file → **InDir**. From a terminal, in the current
directory:

```bash
indir convert the mp4s to webm
indir --yes list every video file          # approve commands up front
indir --prompt 'what is in here?' ~/Downloads
indir --tui "list videos"
indir --doctor
```

`execution.mode = "smart"` (the default) runs read-only inspection unprompted and
asks before anything that writes; `confirm` asks about everything, `auto` about
nothing. Every turn is verified by diffing the folder, so a command that exits 0
without doing the work is reported as not done rather than as success, and
`indir <request>` exits non-zero when the work could not be verified.

Config lives at `~/.config/indir/config.toml` — all options are in
[config.example.toml](config.example.toml). Uninstall with
`./scripts/uninstall.sh`.

## License

MIT
