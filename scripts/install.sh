#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICEMENU_DIR="${HOME}/.local/share/kio/servicemenus"
CONFIG_DIR="${HOME}/.config/indir"
CONFIG_PATH="${CONFIG_DIR}/config.toml"
VENV_DIR="${PROJECT_DIR}/.venv"
LOCAL_BIN="${HOME}/.local/bin"
DESKTOP_FILE="${SERVICEMENU_DIR}/indir.desktop"

# ── helpers ──────────────────────────────────────────────────────────────────

info()  { printf '\033[1;34m→\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m!\033[0m %s\n' "$*"; }
skip()  { printf '\033[1;33m⊘\033[0m Skipped: %s\n' "$*"; }
die()   { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Recreate .venv if it was moved/copied (broken shebangs in bin/*).
venv_needs_recreate() {
  [[ ! -d "$VENV_DIR" ]] && return 1
  [[ ! -x "$VENV_DIR/bin/python" ]] && return 0
  "$VENV_DIR/bin/python" -c 'import sys' &>/dev/null || return 0
  [[ ! -x "$VENV_DIR/bin/pip" ]] && return 0
  "$VENV_DIR/bin/pip" --version &>/dev/null || return 0
  return 1
}

INTERACTIVE=1
FORCE_CONFIG=0
PROVIDER=""
MODEL=""
API_KEY=""
SKIP_DOLPHIN_RESTART=0

# Returns 0 if user consents (or non-interactive / -y auto-accepts).
consent() {
    local title="$1"
    local detail="$2"
    local default="${3:-n}"

    if [[ "$INTERACTIVE" -eq 0 ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        [[ "$default" == "y" ]]
        return
    fi

    echo ""
    echo "  ┌─ ${title}"
    while IFS= read -r line; do
        echo "  │  ${line}"
    done <<< "$detail"
    echo "  └─"

    local prompt="  Proceed?"
    [[ "$default" == "y" ]] && prompt="  Proceed? [Y/n]" || prompt="  Proceed? [y/N]"

    read -rp "$prompt " answer
    answer="${answer:-$default}"
    [[ "${answer,,}" == "y" ]]
}

usage() {
    cat <<EOF
Usage: ./install [options]

One-command setup for InDir on KDE Plasma / Arch.

Interactive mode walks you through each step and asks before making
changes (venv, config, permissions, Dolphin menu, etc.).

Options:
  -y, --yes              Skip prompts and accept all steps (requires --provider and --model)
  --provider PROVIDER    ollama | grok | openai | anthropic | cursor
  --model MODEL          Model ID for the chosen provider (e.g. grok-3, llama3.2, gpt-4o-mini)
  --api-key KEY          API key for cloud backends (avoid; prefer interactive prompt)
  --reconfigure          Overwrite existing config
  --skip-dolphin-restart Don't attempt to restart Dolphin
  -h, --help             Show this help

Examples:
  ./install                                    # interactive: pick backend + model
  ./install --provider grok                      # interactive: pick model + remaining steps
  ./install -y --provider grok --model grok-3  # non-interactive
EOF
}

prompt_provider() {
    echo ""
    echo "  Choose your AI backend (required — no default):"
    echo "    1) Ollama      — local, runs on your machine"
    echo "    2) Grok (xAI)  — cloud"
    echo "    3) OpenAI      — cloud (GPT, compatible APIs)"
    echo "    4) Anthropic   — cloud (Claude)"
    echo "    5) Cursor SDK  — cloud agent"
    echo ""
    while true; do
        read -rp "  Enter choice (1-5): " choice
        case "$choice" in
            1) PROVIDER="ollama"; break ;;
            2) PROVIDER="grok"; break ;;
            3) PROVIDER="openai"; break ;;
            4) PROVIDER="anthropic"; break ;;
            5) PROVIDER="cursor"; break ;;
            *) echo "  Please enter 1, 2, 3, 4, or 5." ;;
        esac
    done
}

prompt_model() {
    echo ""
    echo "  Choose your initial model for ${PROVIDER} (required — no default):"
    case "$PROVIDER" in
        ollama)
            echo "    1) llama3.2"
            echo "    2) llama3.1"
            echo "    3) mistral"
            echo "    4) qwen2.5:7b  (recommended for tool use)"
            echo "    5) Enter a custom model name"
            while true; do
                read -rp "  Enter choice (1-5): " mchoice
                case "$mchoice" in
                    1) MODEL="llama3.2"; break ;;
                    2) MODEL="llama3.1"; break ;;
                    3) MODEL="mistral"; break ;;
                    4) MODEL="qwen2.5:7b"; break ;;
                    5)
                        read -rp "  Model name: " MODEL
                        [[ -n "$MODEL" ]] && break
                        echo "  Model name cannot be empty."
                        ;;
                    *) echo "  Please enter 1, 2, 3, 4, or 5." ;;
                esac
            done
            ;;
        grok)
            echo "    1) grok-3"
            echo "    2) grok-2-1212"
            echo "    3) Enter a custom model name"
            while true; do
                read -rp "  Enter choice (1-3): " mchoice
                case "$mchoice" in
                    1) MODEL="grok-3"; break ;;
                    2) MODEL="grok-2-1212"; break ;;
                    3)
                        read -rp "  Model name: " MODEL
                        [[ -n "$MODEL" ]] && break
                        echo "  Model name cannot be empty."
                        ;;
                    *) echo "  Please enter 1, 2, or 3." ;;
                esac
            done
            ;;
        openai)
            echo "    1) gpt-4o-mini"
            echo "    2) gpt-4o"
            echo "    3) gpt-4.1-mini"
            echo "    4) Enter a custom model name"
            while true; do
                read -rp "  Enter choice (1-4): " mchoice
                case "$mchoice" in
                    1) MODEL="gpt-4o-mini"; break ;;
                    2) MODEL="gpt-4o"; break ;;
                    3) MODEL="gpt-4.1-mini"; break ;;
                    4)
                        read -rp "  Model name: " MODEL
                        [[ -n "$MODEL" ]] && break
                        echo "  Model name cannot be empty."
                        ;;
                    *) echo "  Please enter 1, 2, 3, or 4." ;;
                esac
            done
            ;;
        anthropic)
            echo "    1) claude-sonnet-4-20250514"
            echo "    2) claude-3-5-sonnet-20241022"
            echo "    3) Enter a custom model name"
            while true; do
                read -rp "  Enter choice (1-3): " mchoice
                case "$mchoice" in
                    1) MODEL="claude-sonnet-4-20250514"; break ;;
                    2) MODEL="claude-3-5-sonnet-20241022"; break ;;
                    3)
                        read -rp "  Model name: " MODEL
                        [[ -n "$MODEL" ]] && break
                        echo "  Model name cannot be empty."
                        ;;
                    *) echo "  Please enter 1, 2, or 3." ;;
                esac
            done
            ;;
        cursor)
            echo "    1) composer-2.5"
            echo "    2) Enter a custom model name"
            while true; do
                read -rp "  Enter choice (1-2): " mchoice
                case "$mchoice" in
                    1) MODEL="composer-2.5"; break ;;
                    2)
                        read -rp "  Model name: " MODEL
                        [[ -n "$MODEL" ]] && break
                        echo "  Model name cannot be empty."
                        ;;
                    *) echo "  Please enter 1 or 2." ;;
                esac
            done
            ;;
    esac
    ok "Selected model: ${MODEL}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes) INTERACTIVE=0; shift ;;
        --provider) PROVIDER="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        --reconfigure) FORCE_CONFIG=1; shift ;;
        --skip-dolphin-restart) SKIP_DOLPHIN_RESTART=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
done

# ── banner ───────────────────────────────────────────────────────────────────

echo ""
echo "  InDir — installer"
echo "  Dolphin right-click AI assistant for KDE Plasma"
echo ""
if [[ "$INTERACTIVE" -eq 1 ]] && [[ -t 0 ]]; then
    echo "  Each step below will ask for your consent before making changes."
    echo "  Press Enter to continue, or Ctrl+C to cancel."
    read -r
fi

# ── system checks ────────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    die "python3 not found. Install with: sudo pacman -S python"
fi

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ]]; then
    die "Python 3.11+ required (found ${PY_VERSION})"
fi
ok "Python ${PY_VERSION}"

# ── backend selection ────────────────────────────────────────────────────────

needs_config=0
[[ ! -f "$CONFIG_PATH" ]] && needs_config=1
[[ "$FORCE_CONFIG" -eq 1 ]] && needs_config=1

if [[ "$needs_config" -eq 1 ]] || [[ "$FORCE_CONFIG" -eq 1 ]]; then
    if [[ -z "$PROVIDER" ]]; then
        if [[ "$INTERACTIVE" -eq 1 ]] && [[ -t 0 ]]; then
            prompt_provider
        else
            die "No provider set. Use --provider (ollama|grok|openai|anthropic|cursor) or run interactively."
        fi
    fi

    if [[ -z "$MODEL" ]]; then
        if [[ "$INTERACTIVE" -eq 1 ]] && [[ -t 0 ]]; then
            prompt_model
        else
            die "No model set. Use --model MODEL or run interactively."
        fi
    fi
fi

# ── API key (cloud backends) ─────────────────────────────────────────────────

if [[ "$needs_config" -eq 1 ]] && [[ -z "$API_KEY" ]] && [[ "$PROVIDER" != "ollama" ]]; then
    if [[ "$INTERACTIVE" -eq 1 ]] && [[ -t 0 ]]; then
        if consent "Store API key in config file?" \
            "Your ${PROVIDER} API key can be saved to:
  ${CONFIG_PATH}
The key is stored in plaintext (not encrypted).
You will be asked separately whether to restrict file permissions (chmod 600).
Alternatively, you can skip this and set an API key environment variable instead." \
            "n"; then
            echo ""
            read -rsp "  Enter your ${PROVIDER} API key: " API_KEY
            echo ""
            [[ -z "$API_KEY" ]] && warn "No key entered — configure manually later"
        else
            warn "Skipped API key — set an env var or edit config after install"
        fi
    else
        warn "No API key provided for ${PROVIDER} — set api_key in ${CONFIG_PATH} later"
    fi
fi

# ── Ollama optional setup ────────────────────────────────────────────────────

if [[ "$PROVIDER" == "ollama" ]] && [[ "$INTERACTIVE" -eq 1 ]] && [[ -t 0 ]]; then
    if ! command -v ollama &>/dev/null; then
        if consent "Install Ollama via pacman?" \
            "Ollama runs AI models locally on your machine.
This runs: sudo pacman -S --needed ollama" \
            "n"; then
            sudo pacman -S --needed ollama
        fi
    fi
    if command -v ollama &>/dev/null; then
        if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
            warn "Ollama not running — start with: ollama serve"
        elif ! ollama list 2>/dev/null | grep -qF "${MODEL}"; then
            if consent "Download ${MODEL} model?" \
                "Pulls the ${MODEL} model via Ollama (size varies, may take a while).
This runs: ollama pull ${MODEL}" \
                "y"; then
                info "Pulling ${MODEL}..."
                ollama pull "${MODEL}"
            fi
        fi
    fi
fi

# ── Step 1: venv + package ───────────────────────────────────────────────────

if consent "Install application" \
    "Create a Python virtual environment and install indir:
  Venv:  ${VENV_DIR}
  Runs:  pip install -e ${PROJECT_DIR}" \
    "y"; then

    info "Creating virtual environment..."
    if venv_needs_recreate; then
        warn "Existing virtual environment is unusable (project may have moved) — recreating..."
        rm -rf "$VENV_DIR"
    fi
    if [[ ! -d "$VENV_DIR" ]]; then
        python3 -m venv "$VENV_DIR"
    fi

    info "Installing indir..."
    "$VENV_DIR/bin/pip" install -q --upgrade pip
    # Cursor backend is selectable in the settings UI, so install its extra always.
    "$VENV_DIR/bin/pip" install -q -e "$PROJECT_DIR[cursor]"
    ok "Installed to ${VENV_DIR}/bin/indir"
else
    die "Installation cancelled — application install is required"
fi

CLI_PATH="$VENV_DIR/bin/indir"

# ── Step 2: CLI symlink ──────────────────────────────────────────────────────

if consent "Link CLI to ~/.local/bin" \
    "Create a symlink so you can run 'indir' from your terminal:
  ${LOCAL_BIN}/indir → ${CLI_PATH}
(Dolphin uses the full path directly — this is optional for terminal use.)" \
    "y"; then
    mkdir -p "$LOCAL_BIN"
    ln -sf "$CLI_PATH" "${LOCAL_BIN}/indir"
    ok "Linked ${LOCAL_BIN}/indir"
else
    skip "CLI symlink (use ${CLI_PATH} directly)"
fi

# ── Step 3: shell PATH ───────────────────────────────────────────────────────

if [[ ":$PATH:" != *":${LOCAL_BIN}:"* ]]; then
    SHELL_RC="${HOME}/.bashrc"
    [[ -f "${HOME}/.zshrc" ]] && [[ "${SHELL:-}" == *"zsh"* ]] && SHELL_RC="${HOME}/.zshrc"
    if [[ -f "$SHELL_RC" ]] && ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
        if consent "Add ~/.local/bin to shell PATH" \
            "Append this line to ${SHELL_RC}:
  export PATH=\"\$HOME/.local/bin:\$PATH\"
Only needed if you want 'indir' available in new terminal sessions." \
            "n"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            ok "Added ~/.local/bin to ${SHELL_RC}"
        else
            skip "shell PATH update"
        fi
    fi
fi

# ── Step 4: config file ──────────────────────────────────────────────────────

if [[ "$needs_config" -eq 1 ]]; then
    if consent "Write configuration file" \
        "Create ${CONFIG_PATH}
  Backend: ${PROVIDER}
  Model:   ${MODEL}$([ -n "$API_KEY" ] && echo "
  API key: (will be stored in config)" || echo "")" \
        "y"; then
        info "Writing config..."
        WRITE_ARGS=(--provider "$PROVIDER" --model "$MODEL" --output "$CONFIG_PATH" --force)
        [[ -n "$API_KEY" ]] && WRITE_ARGS+=(--api-key "$API_KEY")
        python3 "$SCRIPT_DIR/write_config.py" "${WRITE_ARGS[@]}"
        ok "Config written to ${CONFIG_PATH}"
    else
        skip "config file — create manually from config.example.toml"
    fi
elif [[ -f "$CONFIG_PATH" ]]; then
    ok "Config already exists (${CONFIG_PATH})"
fi

# ── Step 5: config permissions ───────────────────────────────────────────────

if [[ -f "$CONFIG_PATH" ]]; then
    if consent "Restrict config file permissions" \
        "Run: chmod 600 ${CONFIG_PATH}
This makes the config readable/writable only by you (recommended if it contains an API key)." \
        "y"; then
        chmod 600 "$CONFIG_PATH"
        ok "Config permissions set to 600 (owner only)"
    else
        skip "config chmod — file remains at current permissions"
    fi
fi

# ── Step 6: Dolphin service menu ─────────────────────────────────────────────

LEGACY_DESKTOP_FILE="${SERVICEMENU_DIR}/ai-assistant.desktop"
if [[ -f "$LEGACY_DESKTOP_FILE" ]]; then
    rm -f "$LEGACY_DESKTOP_FILE"
    ok "Removed legacy Dolphin menu (ai-assistant.desktop)"
fi

if consent "Install Dolphin right-click menu" \
    "Create ${DESKTOP_FILE}
Adds an \"InDir\" item when you right-click folders in Dolphin.
The menu runs: ${CLI_PATH} %U" \
    "y"; then
    mkdir -p "$SERVICEMENU_DIR"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Service
MimeType=inode/directory;
Actions=inDir;
X-KDE-Priority=TopLevel
Icon=system-run

[Desktop Action inDir]
Name=InDir
Exec=${CLI_PATH} %U
TryExec=${CLI_PATH}
EOF
    chmod +x "$DESKTOP_FILE"
    ok "Dolphin menu installed"
else
    skip "Dolphin service menu"
fi

# ── Step 7: restart Dolphin ──────────────────────────────────────────────────

if [[ "$SKIP_DOLPHIN_RESTART" -eq 0 ]] && [[ -f "$DESKTOP_FILE" ]]; then
    if pgrep -x dolphin &>/dev/null; then
        if consent "Restart Dolphin" \
            "Quit and reopen Dolphin so the new context menu appears.
Your open Dolphin windows will close." \
            "y"; then
            if command -v kquitapp6 &>/dev/null; then
                info "Restarting Dolphin..."
                kquitapp6 --application=dolphin 2>/dev/null || true
                sleep 1
                dolphin &>/dev/null &
                disown 2>/dev/null || true
                ok "Dolphin restarted"
            else
                warn "kquitapp6 not found — close and reopen Dolphin manually"
            fi
        else
            skip "Dolphin restart — close and reopen Dolphin manually"
        fi
    fi
fi

# ── Step 8: health check ─────────────────────────────────────────────────────

echo ""
if consent "Run installation health check" \
    "Verify CLI, config, Dolphin menu, and backend connectivity." \
    "y"; then
    info "Running checks..."
    "$VENV_DIR/bin/python" "$SCRIPT_DIR/doctor.py" || true
else
    skip "health check — run later with: indir --doctor"
fi

# ── done ─────────────────────────────────────────────────────────────────────

echo ""
ok "Installation complete!"
echo ""
echo "  Use it:  right-click any folder in Dolphin → InDir"
echo "  Test:    ${CLI_PATH} ~/Downloads"
echo "  Reconfigure:  ./install --reconfigure"
echo ""
