#!/usr/bin/env bash
# Runs install.sh and uninstall.sh against a throw-away home folder with fake
# KDE, Flatpak and D-Bus tools, and checks that everything is installed and
# that the original settings come back afterwards.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export HOME="$TMP/home"
export XDG_CONFIG_HOME="$HOME/.config" XDG_DATA_HOME="$HOME/.local/share" XDG_STATE_HOME="$HOME/.local/state"
export NOVA_ALLOW_ROOT=1 XDG_RUNTIME_DIR="$TMP/run" STUB_LOG="$TMP/calls.log" STUB_FLATPAKS="$TMP/flatpaks.txt"
unset DISPLAY WAYLAND_DISPLAY || true
mkdir -p "$HOME/Desktop" "$XDG_CONFIG_HOME" "$XDG_RUNTIME_DIR" "$TMP/bin"

for tool in kwriteconfig6 kreadconfig6 dbus-send flatpak plasma-apply-colorscheme plasma-apply-desktoptheme plasmashell; do
  printf '#!/bin/sh\nexec python3 "%s/tests/stubs/stub.py" %s "$@"\n' "$ROOT" "$tool" > "$TMP/bin/$tool"
  chmod +x "$TMP/bin/$tool"
done
export PATH="$TMP/bin:$PATH"

# "Original" SteamOS-like settings
printf '[General]\nColorScheme=Vapor\n\n[KDE]\nAnimationDurationFactor=1\n' > "$XDG_CONFIG_HOME/kdeglobals"
printf '[Theme]\nname=vapor\n' > "$XDG_CONFIG_HOME/plasmarc"

pass=0
check() { # description, command...
  local desc="$1"; shift
  if "$@"; then pass=$((pass + 1)); printf '  ok   %s\n' "$desc"; else printf '  FAIL %s\n' "$desc"; exit 1; fi
}
kget() { python3 - "$@" <<'EOF'
import configparser, os, sys
cp = configparser.RawConfigParser(strict=False, interpolation=None); cp.optionxform = str
cp.read(os.path.join(os.environ["XDG_CONFIG_HOME"], sys.argv[1]))
print(cp.get(sys.argv[2], sys.argv[3]) if cp.has_option(sys.argv[2], sys.argv[3]) else "<unset>")
EOF
}

echo "== dry run"
bash "$ROOT/install.sh" --yes --dry-run > "$TMP/dry.log"
check "dry run changes nothing" test ! -e "$XDG_DATA_HOME/novadeck"
check "dry run keeps colour scheme" test "$(kget kdeglobals General ColorScheme)" = Vapor

echo "== install"
bash "$ROOT/install.sh" --yes > "$TMP/install.log" 2>&1 || { cat "$TMP/install.log"; exit 1; }
P="$XDG_DATA_HOME/novadeck"
check "app installed" test -f "$P/app/novadeck.py"
check "uninstaller installed" test -x "$P/uninstall.sh"
check "hub launcher" test -f "$XDG_DATA_HOME/applications/novadeck-hub.desktop"
check "netflix launcher" grep -q 'launch netflix' "$XDG_DATA_HOME/applications/novadeck-netflix.desktop"
check "autostart" test -f "$XDG_CONFIG_HOME/autostart/novadeck-hub.desktop"
check "desktop shortcut is executable" test -x "$HOME/Desktop/NovaDeck.desktop"
check "icons" test -f "$XDG_DATA_HOME/icons/hicolor/scalable/apps/novadeck-letter-n.svg"
check "colour scheme file" test -f "$XDG_DATA_HOME/color-schemes/NovaGlass.colors"
check "colour scheme applied" test "$(kget kdeglobals General ColorScheme)" = NovaGlass
check "single click on" test "$(kget kdeglobals KDE SingleClick)" = true
check "splash theme" test "$(kget ksplashrc KSplash Theme)" = com.technova.novadeck.desktop
check "lock screen wallpaper (nested group)" grep -q 'wallpaper.jpg' <(kget kscreenlockerrc 'Greeter][Wallpaper][org.kde.image][General' Image)
check "chrome installed" grep -q com.google.Chrome "$STUB_FLATPAKS"
check "prism installed" grep -q org.prismlauncher.PrismLauncher "$STUB_FLATPAKS"
check "chrome udev override" grep -q 'override --user --filesystem=/run/udev:ro com.google.Chrome' "$STUB_LOG"
check "chrome wallet flag" grep -q -- '--password-store=basic' "$HOME/.var/app/com.google.Chrome/config/chrome-flags.conf"
check "wallpaper set" grep -q "writeConfig('Image', 'file://$P/app/web/img/wallpaper.jpg')" "$STUB_LOG"
check "panel launchers set" grep -q "applications:novadeck-hub.desktop" "$STUB_LOG"
check "original colour scheme recorded" grep -qP '^kdeglobals\tGeneral\tColorScheme\tVapor$' "$XDG_CONFIG_HOME/novadeck/restore.tsv"
check "unset key recorded as unset" grep -qP '^kdeglobals\tKDE\tSingleClick\t__NOVA_UNSET__$' "$XDG_CONFIG_HOME/novadeck/restore.tsv"
check "backup made" test -n "$(find "$XDG_CONFIG_HOME/novadeck/backup" -name kdeglobals -print -quit)"

echo "== reinstall keeps the original values"
bash "$ROOT/install.sh" --yes > "$TMP/install2.log" 2>&1
check "still exactly one ColorScheme record" test "$(grep -cP '^kdeglobals\tGeneral\tColorScheme\t' "$XDG_CONFIG_HOME/novadeck/restore.tsv")" = 1
check "still the original value" grep -qP '^kdeglobals\tGeneral\tColorScheme\tVapor$' "$XDG_CONFIG_HOME/novadeck/restore.tsv"

echo "== self-update mode keeps your choices"
rm -f "$HOME/Desktop/NovaDeck.desktop"
: > "$STUB_LOG"
bash "$ROOT/install.sh" --update > "$TMP/update.log" 2>&1 || { cat "$TMP/update.log"; exit 1; }
check "update: autostart kept" test -f "$XDG_CONFIG_HOME/autostart/novadeck-hub.desktop"
check "update: deleted desktop shortcut stays deleted" test ! -e "$HOME/Desktop/NovaDeck.desktop"
check "update: no app installs" bash -c "! grep -q 'flatpak install' '$STUB_LOG'"
check "update: panel and wallpaper untouched" bash -c "! grep -q evaluateScript '$STUB_LOG'"
check "update: version file written" test "$(cat "$P/VERSION")" = "$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$ROOT/novadeck/version.json")"

echo "== uninstall"
: > "$STUB_LOG"
bash "$P/uninstall.sh" --yes > "$TMP/uninstall.log" 2>&1 || { cat "$TMP/uninstall.log"; exit 1; }
check "app removed" test ! -e "$P"
check "launchers removed" test -z "$(find "$XDG_DATA_HOME/applications" -name 'novadeck*' -print -quit)"
check "autostart removed" test ! -e "$XDG_CONFIG_HOME/autostart/novadeck-hub.desktop"
check "desktop shortcut removed" test ! -e "$HOME/Desktop/NovaDeck.desktop"
check "colour scheme restored" test "$(kget kdeglobals General ColorScheme)" = Vapor
check "plasma style restored" test "$(kget plasmarc Theme name)" = vapor
check "animation speed restored" test "$(kget kdeglobals KDE AnimationDurationFactor)" = 1
check "single click removed again" test "$(kget kdeglobals KDE SingleClick)" = "<unset>"
check "splash removed again" test "$(kget ksplashrc KSplash Theme)" = "<unset>"
check "old wallpaper restored" grep -q "writeConfig('Image', 'file:///usr/share/wallpapers/Steam/steam.jpg')" "$STUB_LOG"
check "old panel launchers restored" grep -q "'applications:steam.desktop','applications:org.kde.dolphin.desktop'" "$STUB_LOG"
check "theme files removed" test ! -e "$XDG_DATA_HOME/color-schemes/NovaGlass.colors"
check "icons removed" test -z "$(find "$XDG_DATA_HOME/icons" -name 'novadeck*' -print -quit 2>/dev/null)"
check "backups kept" test -d "$XDG_CONFIG_HOME/novadeck/backup"

echo "all $pass checks passed"
