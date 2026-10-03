#!/usr/bin/env bash
# Vigil uninstaller.  Matches install.sh: run as the same user (or with sudo for a system-wide install).
#   ./uninstall.sh [--purge]    --purge also deletes ~/.config/vigil
set -euo pipefail

PURGE=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 1 ;;
  esac
done

if [ "$(id -u)" -eq 0 ]; then PREFIX=/usr/local; else PREFIX="$HOME/.local"; fi
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/vigil/

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

gs() {
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$SUDO_USER" env DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$SUDO_USER")/bus" gsettings "$@"
  else
    gsettings "$@"
  fi
}

pkill -f 'python3 -m vigil' 2>/dev/null || true

say "Removing files from $PREFIX"
rm -rf "$PREFIX/share/vigil"
rm -f "$PREFIX/bin/vigil" \
      "$PREFIX/share/applications/vigil.desktop" \
      "$PREFIX/share/icons/hicolor/scalable/apps/vigil.svg"

command -v update-desktop-database >/dev/null && update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
command -v gtk-update-icon-cache  >/dev/null && gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true

if command -v gsettings >/dev/null && gs list-schemas 2>/dev/null | grep -qx "$SCHEMA"; then
  cur="$(gs get "$SCHEMA" custom-keybindings)"
  if [[ "$cur" == *"$BASE"* ]]; then
    new="$(python3 - "$BASE" "$cur" <<'PY'
import ast, sys
base, cur = sys.argv[1], sys.argv[2].replace("@as ", "")
print(repr([x for x in ast.literal_eval(cur) if x != base]))
PY
)"
    for k in name command binding; do gs reset "$SCHEMA.custom-keybinding:$BASE" "$k" 2>/dev/null || true; done
    gs set "$SCHEMA" custom-keybindings "$new"
    say "Removed Ctrl+Shift+Esc shortcut"
  fi
fi

if [ "$PURGE" -eq 1 ]; then
  rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/vigil"
  say "Removed config"
fi
say "Vigil uninstalled."
