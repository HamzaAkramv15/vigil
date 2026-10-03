#!/usr/bin/env bash
# Vigil installer.  Per-user by default (~/.local); run with sudo for a system-wide install (/usr/local).
#   ./install.sh [--no-shortcut] [--no-deps]
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHORTCUT=1; DEPS=1
for a in "$@"; do
  case "$a" in
    --no-shortcut) SHORTCUT=0 ;;
    --no-deps)     DEPS=0 ;;
    -h|--help)     sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 1 ;;
  esac
done

if [ "$(id -u)" -eq 0 ]; then PREFIX=/usr/local; else PREFIX="$HOME/.local"; fi
APPDIR="$PREFIX/share/vigil"
BIN="$PREFIX/bin/vigil"
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/vigil/
KEYS='<Primary><Shift>Escape'

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# gsettings must talk to the *user's* session bus, even under sudo
gs() {
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$SUDO_USER" env DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$SUDO_USER")/bus" gsettings "$@"
  else
    gsettings "$@"
  fi
}

# ---- dependencies --------------------------------------------------------
if [ "$DEPS" -eq 1 ]; then
  if ! python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk; import psutil, cairo' 2>/dev/null; then
    say "Installing dependencies (needs sudo)…"
    SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO=sudo
    $SUDO apt-get update -qq
    $SUDO apt-get install -y python3 python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-3.0 \
      python3-psutil libnotify-bin pciutils policykit-1 || warn "apt failed; install the packages manually (see README)."
  fi
  # Optional extras: panel indicator + Intel iGPU load for the privileged helper. Failures are fine.
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO=sudo
  python3 -c 'import gi; gi.require_version("AyatanaAppIndicator3","0.1")' 2>/dev/null || \
    $SUDO apt-get install -y gir1.2-ayatanaappindicator3-0.1 2>/dev/null || warn "Panel indicator not available (optional)."
  command -v intel_gpu_top >/dev/null || ! grep -qs 0x8086 /sys/bus/pci/devices/*/vendor || \
    $SUDO apt-get install -y intel-gpu-tools 2>/dev/null || true
fi

# ---- files ---------------------------------------------------------------
say "Installing to $PREFIX"
mkdir -p "$APPDIR" "$PREFIX/bin" "$PREFIX/share/applications" "$PREFIX/share/icons/hicolor/scalable/apps"
rm -rf "$APPDIR/vigil"
cp -r "$SRC/vigil" "$APPDIR/vigil"
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +

cat > "$BIN" <<LAUNCH
#!/bin/sh
PYTHONPATH="$APPDIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m vigil "\$@"
LAUNCH
chmod 755 "$BIN"

install -m 644 "$SRC/data/vigil.svg" "$PREFIX/share/icons/hicolor/scalable/apps/vigil.svg"
sed "s|@BIN@|$BIN|g" "$SRC/data/vigil.desktop.in" > "$PREFIX/share/applications/vigil.desktop"
chmod 644 "$PREFIX/share/applications/vigil.desktop"

command -v update-desktop-database >/dev/null && update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
command -v gtk-update-icon-cache  >/dev/null && gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true

# ---- keyboard shortcut (Unity / GNOME-style settings daemon) -------------
if [ "$SHORTCUT" -eq 1 ]; then
  if command -v gsettings >/dev/null && gs list-schemas 2>/dev/null | grep -qx "$SCHEMA"; then
    cur="$(gs get "$SCHEMA" custom-keybindings)"
    new="$(python3 - "$BASE" "$cur" <<'PY'
import ast, sys
base, cur = sys.argv[1], sys.argv[2].replace("@as ", "")
lst = ast.literal_eval(cur)
if base not in lst:
    lst.append(base)
print(repr(lst))
PY
)"
    gs set "$SCHEMA" custom-keybindings "$new"
    K="$SCHEMA.custom-keybinding:$BASE"
    gs set "$K" name 'Vigil System Monitor'
    gs set "$K" command "$BIN"
    gs set "$K" binding "$KEYS"
    say "Shortcut set: Ctrl+Shift+Esc  (change it in System Settings > Keyboard > Shortcuts)"
  else
    warn "No supported shortcut settings found; bind '$BIN' to a key manually."
  fi
fi

case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) warn "$PREFIX/bin is not on your PATH (log out/in, or add it)." ;; esac
say "Done. Launch 'Vigil' from the Dash, or run: vigil"
