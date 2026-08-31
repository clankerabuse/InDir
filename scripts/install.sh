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
SKIP_DOLPHIN_RESTART=0
SKIP_THUNAR_RESTART=0

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

One-command setup for InDir on Arch (Dolphin and/or Thunar).

Installs the app and file-manager menus. Backend, model, and API keys
are configured later in the InDir settings UI (gear icon).

Interactive mode walks you through each step and asks before making
changes (venv, starter config, permissions, file-manager menus, etc.).

Options:
  -y, --yes              Skip prompts and accept all steps
  --reset-config         Overwrite config with starter defaults
  --skip-dolphin-restart Don't attempt to restart Dolphin
  --skip-thunar-restart  Don't attempt to restart Thunar
  -h, --help             Show this help

Examples:
  ./install       # interactive
  ./install -y    # non-interactive
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes) INTERACTIVE=0; shift ;;
        --reset-config|--reconfigure) FORCE_CONFIG=1; shift ;;
        --skip-dolphin-restart) SKIP_DOLPHIN_RESTART=1; shift ;;
        --skip-thunar-restart) SKIP_THUNAR_RESTART=1; shift ;;
        --provider|--model|--api-key)
            die "Backend options removed — choose provider/model/API key in the InDir settings UI after install"
            ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
done

# ── banner ───────────────────────────────────────────────────────────────────

echo ""
echo "  InDir — installer"
echo "  Right-click AI assistant for Dolphin and Thunar"
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
(File managers use the full path directly — this is optional for terminal use.)" \
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

# ── Step 4: starter config ───────────────────────────────────────────────────

needs_config=0
[[ ! -f "$CONFIG_PATH" ]] && needs_config=1
[[ "$FORCE_CONFIG" -eq 1 ]] && needs_config=1

if [[ "$needs_config" -eq 1 ]]; then
    if consent "Write starter configuration" \
        "Create ${CONFIG_PATH} with defaults.
Provider, model, and API keys are set later in the InDir settings UI (gear icon)." \
        "y"; then
        info "Writing starter config..."
        WRITE_ARGS=(--output "$CONFIG_PATH")
        [[ "$FORCE_CONFIG" -eq 1 ]] && WRITE_ARGS+=(--force)
        python3 "$SCRIPT_DIR/write_config.py" "${WRITE_ARGS[@]}"
        ok "Starter config written to ${CONFIG_PATH}"
    else
        skip "config file — create later from settings or config.example.toml"
    fi
elif [[ -f "$CONFIG_PATH" ]]; then
    ok "Config already exists (${CONFIG_PATH})"
fi

# ── Step 5: config permissions ───────────────────────────────────────────────

if [[ -f "$CONFIG_PATH" ]]; then
    if consent "Restrict config file permissions" \
        "Run: chmod 600 ${CONFIG_PATH}
Recommended once you store an API key via settings." \
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

# ── Step 6b: Thunar custom action ────────────────────────────────────────────

THUNAR_UCA="${HOME}/.config/Thunar/uca.xml"
HAS_THUNAR=0
command -v thunar &>/dev/null && HAS_THUNAR=1
THUNAR_DEFAULT="n"
[[ "$HAS_THUNAR" -eq 1 ]] && THUNAR_DEFAULT="y"

THUNAR_INSTALLED=0
if [[ "$HAS_THUNAR" -eq 0 ]] && [[ "$INTERACTIVE" -eq 0 ]]; then
    skip "Thunar custom action (Thunar not installed)"
elif consent "Install Thunar right-click menu" \
    "Add an \"InDir\" custom action to:
  ${THUNAR_UCA}
Existing Thunar actions are left in place.
The menu runs: ${CLI_PATH} %f" \
    "$THUNAR_DEFAULT"; then
    python3 "$SCRIPT_DIR/thunar_uca.py" install --cli "$CLI_PATH" --uca "$THUNAR_UCA"
    THUNAR_INSTALLED=1
    ok "Thunar menu installed"
else
    skip "Thunar custom action"
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

# ── Step 7b: restart Thunar ──────────────────────────────────────────────────

if [[ "$SKIP_THUNAR_RESTART" -eq 0 ]] && [[ "$THUNAR_INSTALLED" -eq 1 ]]; then
    if pgrep -x thunar &>/dev/null; then
        if consent "Restart Thunar" \
            "Quit Thunar so the new custom action appears.
Open Thunar windows will close (thunar -q)." \
            "y"; then
            info "Restarting Thunar..."
            thunar -q 2>/dev/null || true
            sleep 1
            thunar &>/dev/null &
            disown 2>/dev/null || true
            ok "Thunar restarted"
        else
            skip "Thunar restart — close and reopen Thunar manually"
        fi
    fi
fi

# ── Step 8: health check ─────────────────────────────────────────────────────

echo ""
if consent "Run installation health check" \
    "Verify CLI, starter config, and file-manager menus.
Backend connectivity is checked after you configure settings." \
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
echo "  Use it:  right-click any folder in Dolphin or Thunar → InDir"
echo "  Then:    open the gear icon → pick backend, model, and API key"
echo "  Test:    ${CLI_PATH} ~/Downloads"
echo ""
