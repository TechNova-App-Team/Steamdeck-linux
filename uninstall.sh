#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  NovaDeck uninstaller
#  Removes NovaDeck and puts your previous desktop settings back.
#  Apps installed from Flathub (Chrome, Prism Launcher …) are kept; remove
#  them in Discover if you don't need them anymore.
#
#  Usage:  uninstall.sh [--yes] [--keep-settings]
# ---------------------------------------------------------------------------
set -euo pipefail

main() {
  local CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
  local DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
  local PREFIX="$DATA_HOME/novadeck"
  local NOVA_CONF="$CONFIG_HOME/novadeck"
  local RESTORE_FILE="$NOVA_CONF/restore.tsv"
  local UNSET="__NOVA_UNSET__"
  local ASSUME_YES=0 KEEP_SETTINGS=0 arg

  for arg in "$@"; do
    case "$arg" in
      -y|--yes) ASSUME_YES=1 ;;
      --keep-settings) KEEP_SETTINGS=1 ;;
      -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; return 0 ;;
      *) echo "Unbekannte Option: $arg" >&2; return 1 ;;
    esac
  done

  local B="" W="" G="" Y="" N=""
  if [[ -t 1 ]]; then B=$'\e[1m'; W=$'\e[97m'; G=$'\e[92m'; Y=$'\e[93m'; N=$'\e[0m'; fi
  ok() { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
  warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }

  printf '\n%s  ✦  NovaDeck entfernen%s\n\n' "$B$W" "$N"
  if (( ! ASSUME_YES )); then
    if [[ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]] && command -v kdialog >/dev/null 2>&1; then
      kdialog --title "NovaDeck" --warningcontinuecancel \
        "NovaDeck entfernen und dein vorheriges Desktop-Design wiederherstellen?" || { echo "  Abgebrochen."; return 0; }
    elif [[ -t 0 ]]; then
      local answer
      read -r -p "  NovaDeck entfernen und altes Design wiederherstellen? [j/N] " answer
      [[ "$answer" =~ ^[jJyY] ]] || { echo "  Abgebrochen."; return 0; }
    fi
  fi

  local KWRITE KNOTIFY=()
  KWRITE="$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)"
  if [[ -n "$KWRITE" ]] && "$KWRITE" --help 2>&1 | grep -q -- '--notify'; then KNOTIFY=(--notify); fi
  local PY
  PY="$(command -v python3 || true)"

  plasma_eval() {
    dbus-send --session --print-reply=literal --dest=org.kde.plasmashell /PlasmaShell \
      org.kde.PlasmaShell.evaluateScript "string:$1" >/dev/null 2>&1 || true
  }

  # 1) Stop the hub and remove launchers
  if [[ -n "$PY" && -f "$PREFIX/app/novadeck.py" ]]; then
    "$PY" "$PREFIX/app/novadeck.py" stop >/dev/null 2>&1 || true
    "$PY" "$PREFIX/app/novadeck.py" remove-desktop-files >/dev/null 2>&1 || true
  fi
  rm -f "$DATA_HOME/applications/novadeck-uninstall.desktop" "$CONFIG_HOME/autostart/novadeck-hub.desktop"
  local desk
  desk="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
  if grep -qs 'X-NovaDeck=true' "$desk/NovaDeck.desktop"; then rm -f "$desk/NovaDeck.desktop"; fi
  ok "Hub beendet, Starter entfernt"

  # 2) Restore every setting NovaDeck changed, newest first
  local colorscheme="" desktoptheme=""
  if [[ -f "$RESTORE_FILE" ]]; then
    local file group key value
    while IFS=$'\t' read -r file group key value; do
      [[ -z "$file" ]] && continue
      case "$file" in
        @wallpaper)
          if [[ "$value" != "$UNSET" && -n "$value" && "$value" != *"'"* ]]; then
            plasma_eval "desktops().forEach(function (d) { d.wallpaperPlugin = 'org.kde.image'; d.currentConfigGroup = ['Wallpaper','org.kde.image','General']; d.writeConfig('Image', '$value'); });"
          fi
          ;;
        @launchers)
          local panel="${group%%:*}" widget="${group#*:}" list="" item
          local -a items=()
          IFS=',' read -r -a items <<< "$value"
          for item in "${items[@]}"; do [[ -n "$item" && "$item" != *"'"* ]] && list+="'$item',"; done
          list="${list%,}"
          plasma_eval "panels().forEach(function (p) { if (String(p.id) != '$panel') return; p.widgets().forEach(function (w) { if (String(w.id) == '$widget') { w.currentConfigGroup = ['General']; w.writeConfig('launchers', [$list]); if (w.reloadConfig) { w.reloadConfig(); } } }); });"
          ;;
        *)
          [[ -z "$KWRITE" ]] && continue
          if [[ "$file" == kdeglobals && "$group" == General && "$key" == ColorScheme ]]; then colorscheme="$value"; fi
          if [[ "$file" == plasmarc && "$group" == Theme && "$key" == name ]]; then desktoptheme="$value"; fi
          local -a groups=()
          local IFS_OLD="$IFS" part
          IFS='/'
          for part in $group; do groups+=(--group "$part"); done
          IFS="$IFS_OLD"
          if [[ "$value" == "$UNSET" ]]; then
            "$KWRITE" "${KNOTIFY[@]}" --file "$file" "${groups[@]}" --key "$key" --delete >/dev/null 2>&1 || true
          else
            "$KWRITE" "${KNOTIFY[@]}" --file "$file" "${groups[@]}" --key "$key" "$value" >/dev/null 2>&1 || true
          fi
          ;;
      esac
    done < <(tac "$RESTORE_FILE")

    if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
      [[ -z "$colorscheme" || "$colorscheme" == "$UNSET" ]] && colorscheme="BreezeLight"
      plasma-apply-colorscheme "$colorscheme" >/dev/null 2>&1 || plasma-apply-colorscheme BreezeDark >/dev/null 2>&1 || true
    fi
    if command -v plasma-apply-desktoptheme >/dev/null 2>&1; then
      [[ -z "$desktoptheme" || "$desktoptheme" == "$UNSET" ]] && desktoptheme="default"
      plasma-apply-desktoptheme "$desktoptheme" >/dev/null 2>&1 || true
    fi
    dbus-send --session --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure >/dev/null 2>&1 || true
    ok "Vorheriges Design wiederhergestellt"
  else
    warn "Keine gespeicherten Einstellungen gefunden – Design bleibt, wie es ist."
  fi

  # 3) Remove files
  rm -f "$DATA_HOME/color-schemes/NovaGlass.colors" \
        "$DATA_HOME/konsole/NovaGlass.colorscheme" "$DATA_HOME/konsole/NovaGlass.profile"
  rm -rf "$DATA_HOME/plasma/look-and-feel/com.technova.novadeck.desktop"
  rm -f "$DATA_HOME"/icons/hicolor/scalable/apps/novadeck*.svg
  rm -rf "$PREFIX"
  if (( KEEP_SETTINGS )); then
    ok "Einstellungen behalten in $NOVA_CONF"
  else
    rm -f "$RESTORE_FILE" "$NOVA_CONF/state.json"
    ok "Dateien entfernt (Sicherungen bleiben in $NOVA_CONF/backup)"
  fi
  command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true

  printf '\n  %sFertig.%s Installierte Apps (Chrome, Prism Launcher …) bleiben erhalten.\n' "$B" "$N"
  printf '  Zum Entfernen einfach im App Store (Discover) auf „Entfernen“ tippen.\n\n'
}

main "$@"
