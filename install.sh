#!/usr/bin/env bash
# shellcheck disable=SC1111  # German quotation marks in messages are intended
# ---------------------------------------------------------------------------
#  NovaDeck installer
#  Makes the Steam Deck desktop mode clean, glassy and easy to use.
#
#  * runs as the normal "deck" user: no sudo, no password, no read-only tricks
#  * everything lives in your home folder, so SteamOS updates keep it
#  * the Gaming Mode / Big Picture stays exactly as it is
#  * every change can be undone:  ~/.local/share/novadeck/uninstall.sh
#
#  Usage:  ./install.sh [--yes] [--dry-run] [--no-theme] [--no-apps]
#                       [--no-autostart] [--no-lock-screen]
# ---------------------------------------------------------------------------
set -euo pipefail

NOVA_VERSION="1.0.0"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Double-clicked in Dolphin? Re-open inside Konsole so you can follow along.
if [[ ! -t 1 && -z "${NOVA_IN_TERMINAL:-}" ]] && command -v konsole >/dev/null 2>&1; then
  export NOVA_IN_TERMINAL=1
  exec konsole --hold -e bash "$SRC_DIR/install.sh" "$@"
fi

CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
PREFIX="$DATA_HOME/novadeck"
NOVA_CONF="$CONFIG_HOME/novadeck"
RESTORE_FILE="$NOVA_CONF/restore.tsv"
BACKUP_DIR="$NOVA_CONF/backup/$(date +%Y%m%d-%H%M%S)"
LOG_DIR="$STATE_HOME/novadeck"
UNSET="__NOVA_UNSET__"
FLATHUB="https://dl.flathub.org/repo/flathub.flatpakrepo"

ASSUME_YES=0
DRY_RUN=0
WANT_THEME=1
WANT_APPS=1
WANT_AUTOSTART=1
WANT_NOLOCK=0
SELECTED_APPS=(com.google.Chrome org.prismlauncher.PrismLauncher)

# ---------------------------------------------------------------- output
if [[ -t 1 ]]; then
  B=$'\e[1m'; D=$'\e[2m'; W=$'\e[97m'; G=$'\e[92m'; Y=$'\e[93m'; R=$'\e[91m'; N=$'\e[0m'
else
  B=""; D=""; W=""; G=""; Y=""; R=""; N=""
fi
step() { printf '\n%s◆ %s%s\n' "$B$W" "$1" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
info() { printf '  %s·%s %s\n' "$D" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
fail() { printf '\n%s✗ %s%s\n' "$R$B" "$1" "$N" >&2; exit 1; }

run() {
  if (( DRY_RUN )); then
    printf '  %s$ %s%s\n' "$D" "$*" "$N"
  else
    "$@"
  fi
}

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-theme) WANT_THEME=0 ;;
    --no-apps) WANT_APPS=0 ;;
    --no-autostart) WANT_AUTOSTART=0 ;;
    --no-lock-screen) WANT_NOLOCK=1 ;;
    -h|--help) usage ;;
    *) fail "Unbekannte Option: $arg (siehe --help)" ;;
  esac
done

# ---------------------------------------------------------------- helpers
KWRITE="$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)"
KREAD="$(command -v kreadconfig6 || command -v kreadconfig5 || true)"
KNOTIFY=()
if [[ -n "$KWRITE" ]] && "$KWRITE" --help 2>&1 | grep -q -- '--notify'; then KNOTIFY=(--notify); fi
PY="$(command -v python3 || true)"

has_gui() { [[ -n "${WAYLAND_DISPLAY:-}" || -n "${DISPLAY:-}" ]]; }

plasma_running() {
  command -v dbus-send >/dev/null 2>&1 || return 1
  dbus-send --session --print-reply --dest=org.freedesktop.DBus / \
    org.freedesktop.DBus.NameHasOwner string:org.kde.plasmashell 2>/dev/null | grep -q true
}

plasma_eval() {
  # Runs a Plasma desktop script and prints what it print()s.
  dbus-send --session --print-reply=literal --dest=org.kde.plasmashell /PlasmaShell \
    org.kde.PlasmaShell.evaluateScript "string:$1" 2>/dev/null | sed 's/^ *//'
}

group_args() { # "A/B/C" -> --group A --group B --group C
  local IFS='/' part
  for part in $1; do printf '%s\0%s\0' --group "$part"; done
}

recorded() { [[ -f "$RESTORE_FILE" ]] && grep -qF -- "$1"$'\t'"$2"$'\t'"$3"$'\t' "$RESTORE_FILE"; }

record() { # file group key value  (keeps the very first value = your original setting)
  recorded "$1" "$2" "$3" && return 0
  (( DRY_RUN )) && return 0
  mkdir -p "$NOVA_CONF"
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$RESTORE_FILE"
}

kget() { # file group key
  local -a groups
  mapfile -d '' groups < <(group_args "$2")
  "$KREAD" --file "$1" "${groups[@]}" --key "$3" --default "$UNSET" 2>/dev/null || printf '%s' "$UNSET"
}

kset() { # file group key value
  local -a groups
  mapfile -d '' groups < <(group_args "$2")
  record "$1" "$2" "$3" "$(kget "$1" "$2" "$3")"
  run "$KWRITE" "${KNOTIFY[@]}" --file "$1" "${groups[@]}" --key "$3" "$4"
}

desktop_id_exists() {
  local id="$1" dir
  for dir in "$DATA_HOME/applications" /usr/share/applications /usr/local/share/applications \
             "$DATA_HOME/flatpak/exports/share/applications" /var/lib/flatpak/exports/share/applications; do
    [[ -f "$dir/$id" ]] && return 0
  done
  return 1
}

# ---------------------------------------------------------------- choices
choose() {
  (( ASSUME_YES )) && return 0
  if has_gui && command -v kdialog >/dev/null 2>&1; then
    local picked
    if ! picked="$(kdialog --title "NovaDeck $NOVA_VERSION" --geometry 640x520 --checklist \
      "Was soll NovaDeck für dich einrichten?\n\nAlles lässt sich später wieder entfernen." \
      theme     "Glas-Design für den Desktop (Farben, Wallpaper, Leiste, Startbild)" on \
      autostart "NovaDeck automatisch öffnen, wenn du in den Desktop-Modus wechselst" on \
      chrome    "Google Chrome: für Netflix, YouTube, Fortnite (Cloud) & Co." on \
      prism     "Prism Launcher: Minecraft (Java Edition)" on \
      spotify   "Spotify" off \
      discord   "Discord" off \
      heroic    "Heroic Games Launcher: Epic, GOG & Amazon Spiele" off \
      vlc       "VLC: Videos & Musik von SD-Karte oder USB" off \
      nolock    "Bildschirmsperre im Desktop-Modus abschalten" off)"; then
      fail "Abgebrochen. Es wurde nichts verändert."
    fi
    apply_choices "$picked"
    return 0
  fi
  if [[ -t 0 ]]; then
    local picked="" answer
    ask() { read -r -p "  $2 [${3}] " answer; answer="${answer:-$3}"; [[ "$answer" =~ ^[jJyY] ]] && picked+=" \"$1\""; }
    printf '\n  %sWas soll eingerichtet werden?%s (Enter = Vorschlag)\n' "$B" "$N"
    ask theme "Glas-Design für den Desktop?" j
    ask autostart "Hub beim Desktop-Start öffnen?" j
    ask chrome "Google Chrome (Netflix, YouTube, Fortnite-Cloud)?" j
    ask prism "Prism Launcher (Minecraft)?" j
    ask spotify "Spotify?" n
    ask discord "Discord?" n
    ask heroic "Heroic Games Launcher?" n
    ask vlc "VLC?" n
    ask nolock "Bildschirmsperre abschalten?" n
    apply_choices "$picked"
  fi
}

apply_choices() {
  local picked="$1"
  has() { [[ "$picked" == *"\"$1\""* ]]; }
  WANT_THEME=0; WANT_AUTOSTART=0; WANT_NOLOCK=0; SELECTED_APPS=()
  has theme && WANT_THEME=1
  has autostart && WANT_AUTOSTART=1
  has nolock && WANT_NOLOCK=1
  has chrome && SELECTED_APPS+=(com.google.Chrome)
  has prism && SELECTED_APPS+=(org.prismlauncher.PrismLauncher)
  has spotify && SELECTED_APPS+=(com.spotify.Client)
  has discord && SELECTED_APPS+=(com.discordapp.Discord)
  has heroic && SELECTED_APPS+=(com.heroicgameslauncher.hgl)
  has vlc && SELECTED_APPS+=(org.videolan.VLC)
  return 0
}

# ---------------------------------------------------------------- steps
preflight() {
  step "Systemcheck"
  [[ $EUID -eq 0 && -z "${NOVA_ALLOW_ROOT:-}" ]] && fail "Bitte NICHT mit sudo starten. Einfach als normaler Nutzer: ./install.sh"
  [[ -f "$SRC_DIR/novadeck/novadeck.py" ]] || fail "Dateien unvollständig. Bitte den ganzen Ordner entpacken und install.sh von dort starten."
  if grep -qs '^ID=steamos' /etc/os-release; then ok "SteamOS erkannt"; else warn "Kein SteamOS – NovaDeck läuft trotzdem auf KDE Plasma."; fi
  if [[ -n "$PY" ]]; then ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])')"; else warn "python3 fehlt: Der Hub läuft dann nicht, Design & Apps schon."; fi
  if command -v plasmashell >/dev/null 2>&1; then ok "$(plasmashell --version 2>/dev/null | head -1)"; fi
  if [[ -z "$KWRITE" ]]; then
    warn "kwriteconfig fehlt – Design wird übersprungen."
    WANT_THEME=0
  fi
  if ! command -v flatpak >/dev/null 2>&1; then
    warn "flatpak fehlt – Apps werden übersprungen."
    WANT_APPS=0
  fi
  (( DRY_RUN )) && info "Probelauf: Es wird nichts verändert."
  return 0
}

install_files() {
  step "NovaDeck installieren"
  if [[ -n "$PY" && -f "$PREFIX/app/novadeck.py" ]] && (( ! DRY_RUN )); then
    "$PY" "$PREFIX/app/novadeck.py" stop >/dev/null 2>&1 || true
  fi
  run mkdir -p "$PREFIX" "$LOG_DIR" "$NOVA_CONF"
  run rm -rf "$PREFIX/app.new"
  run cp -r "$SRC_DIR/novadeck" "$PREFIX/app.new"
  run find "$PREFIX/app.new" -name '__pycache__' -prune -exec rm -rf {} +
  run rm -rf "$PREFIX/app" "$PREFIX/theme"
  run mv "$PREFIX/app.new" "$PREFIX/app"
  run cp -r "$SRC_DIR/theme" "$PREFIX/theme"
  run install -m 0755 "$SRC_DIR/uninstall.sh" "$PREFIX/uninstall.sh"
  (( DRY_RUN )) || printf '%s\n' "$NOVA_VERSION" > "$PREFIX/VERSION"
  ok "Programm → $PREFIX"

  local icons="$DATA_HOME/icons/hicolor/scalable/apps"
  run mkdir -p "$icons"
  run cp "$SRC_DIR"/assets/icons/novadeck*.svg "$icons/"
  ok "Icons installiert"

  if [[ -n "$PY" ]]; then
    local autostart=off
    (( WANT_AUTOSTART )) && autostart=on
    run "$PY" "$PREFIX/app/novadeck.py" desktop-files --autostart "$autostart" >/dev/null
    ok "Starter im App-Menü (Netflix, YouTube, Fortnite …)"
    (( WANT_AUTOSTART )) && ok "Hub öffnet sich beim Desktop-Start"
  fi

  # Uninstaller in the app menu (kept out of the hub's own launcher cleanup on purpose).
  if (( ! DRY_RUN )); then
    mkdir -p "$DATA_HOME/applications"
    cat > "$DATA_HOME/applications/novadeck-uninstall.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NovaDeck entfernen
Comment=Entfernt NovaDeck und stellt dein altes Desktop-Design wieder her
Exec=konsole --hold -e bash "$PREFIX/uninstall.sh"
Icon=novadeck
Terminal=false
Categories=System;
X-NovaDeck-Uninstall=true
EOF
  fi

  # Shortcut on the desktop, next to "Return to Gaming Mode".
  local desk
  desk="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
  if [[ -d "$desk" && -f "$DATA_HOME/applications/novadeck-hub.desktop" ]]; then
    run install -m 0755 "$DATA_HOME/applications/novadeck-hub.desktop" "$desk/NovaDeck.desktop"
    ok "Verknüpfung auf dem Desktop"
  fi
  command -v kbuildsycoca6 >/dev/null 2>&1 && run kbuildsycoca6 >/dev/null 2>&1 || true
  command -v kbuildsycoca5 >/dev/null 2>&1 && run kbuildsycoca5 >/dev/null 2>&1 || true
  return 0
}

install_apps() {
  (( WANT_APPS )) || return 0
  (( ${#SELECTED_APPS[@]} )) || return 0
  step "Apps aus Flathub"
  run flatpak remote-add --user --if-not-exists flathub "$FLATHUB"
  local app
  for app in "${SELECTED_APPS[@]}"; do
    if flatpak info "$app" >/dev/null 2>&1; then
      ok "$app ist schon da"
      continue
    fi
    info "$app wird installiert …"
    if run flatpak install --user -y --noninteractive flathub "$app"; then
      ok "$app"
    else
      warn "$app ließ sich nicht installieren (Internet?). Geht später auch per Klick im Hub."
    fi
  done

  if flatpak info com.google.Chrome >/dev/null 2>&1 || (( DRY_RUN )); then
    # Controllers for cloud gaming in the browser (Valve's own Chrome shortcut does the same).
    run flatpak override --user --filesystem=/run/udev:ro com.google.Chrome
    # Fresh Chrome profile: skip the KDE-Wallet password prompt on every start.
    local cdir="$HOME/.var/app/com.google.Chrome/config"
    if [[ ! -d "$cdir/google-chrome/Default" ]] && ! grep -qs -- '--password-store' "$cdir/chrome-flags.conf"; then
      run mkdir -p "$cdir"
      (( DRY_RUN )) || printf '%s\n' '--password-store=basic' >> "$cdir/chrome-flags.conf"
      ok "Chrome fragt nicht mehr nach dem KDE-Wallet-Passwort"
    fi
    ok "Controller-Zugriff für Cloud-Gaming im Browser"
  fi
  return 0
}

backup_configs() {
  (( DRY_RUN )) && return 0
  mkdir -p "$BACKUP_DIR"
  local f
  for f in kdeglobals kwinrc plasmarc plasmashellrc plasma-org.kde.plasma.desktop-appletsrc ksplashrc konsolerc kscreenlockerrc; do
    [[ -f "$CONFIG_HOME/$f" ]] && cp -a "$CONFIG_HOME/$f" "$BACKUP_DIR/"
  done
  ok "Sicherung deiner Einstellungen → $BACKUP_DIR"
}

apply_theme() {
  (( WANT_THEME )) || return 0
  step "Glas-Design"
  backup_configs

  run mkdir -p "$DATA_HOME/color-schemes" "$DATA_HOME/konsole" "$DATA_HOME/plasma/look-and-feel"
  run cp "$SRC_DIR/theme/color-schemes/NovaGlass.colors" "$DATA_HOME/color-schemes/"
  run cp "$SRC_DIR/theme/konsole/NovaGlass.colorscheme" "$SRC_DIR/theme/konsole/NovaGlass.profile" "$DATA_HOME/konsole/"
  run rm -rf "$DATA_HOME/plasma/look-and-feel/com.technova.novadeck.desktop"
  run cp -r "$SRC_DIR/theme/look-and-feel/com.technova.novadeck.desktop" "$DATA_HOME/plasma/look-and-feel/"

  # Colours: black/grey with neon-white highlights
  record kdeglobals General ColorScheme "$(kget kdeglobals General ColorScheme)"
  if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
    run plasma-apply-colorscheme NovaGlass >/dev/null || warn "Farbschema ließ sich nicht live setzen."
  else
    kset kdeglobals General ColorScheme NovaGlass
  fi
  ok "Farbschema „Nova Glass“"

  # Plasma style "Breeze" follows the colour scheme -> glassy dark panel & popups
  record plasmarc Theme name "$(kget plasmarc Theme name)"
  if command -v plasma-apply-desktoptheme >/dev/null 2>&1; then
    run plasma-apply-desktoptheme default >/dev/null || true
  else
    kset plasmarc Theme name default
  fi
  kset kdeglobals Icons Theme breeze-dark
  ok "Leisten & Menüs im Glas-Look"

  # Real blur behind translucent panels, menus and the terminal
  kset kwinrc Plugins blurEnabled true
  kset kwinrc Plugins contrastEnabled true
  kset kwinrc Effect-blur BlurStrength 10
  kset kwinrc Effect-blur NoiseStrength 3
  # Easier with touch & trackpad: open files with a single tap, snappier animations
  kset kdeglobals KDE SingleClick true
  kset kdeglobals KDE AnimationDurationFactor 0.6
  ok "Blur-Effekt, schnellere Animationen, Einfach-Klick"

  # Splash screen when switching to the desktop
  kset ksplashrc KSplash Engine KSplashQML
  kset ksplashrc KSplash Theme com.technova.novadeck.desktop
  ok "NovaDeck-Startbildschirm"

  # Terminal
  kset konsolerc "Desktop Entry" DefaultProfile NovaGlass.profile
  ok "Konsole im Glas-Look"

  local wall="$PREFIX/app/web/img/wallpaper.jpg"
  kset kscreenlockerrc "Greeter/Wallpaper/org.kde.image/General" Image "file://$wall"
  if (( WANT_NOLOCK )); then
    kset kscreenlockerrc Daemon Autolock false
    kset kscreenlockerrc Daemon LockOnResume false
    ok "Bildschirmsperre im Desktop-Modus aus"
  fi

  if plasma_running; then
    apply_plasma_desktop "$wall"
  else
    warn "Plasma läuft gerade nicht – Wallpaper & Leiste werden beim nächsten Start gesetzt."
  fi

  if command -v dbus-send >/dev/null 2>&1; then
    run dbus-send --session --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure >/dev/null 2>&1 || true
  fi
  return 0
}

apply_plasma_desktop() {
  local wall="$1"

  # Remember the current wallpaper (only the very first time).
  if ! recorded @wallpaper - -; then
    local old_wall
    old_wall="$(plasma_eval 'var d = desktops(); var out = ""; if (d.length) { d[0].currentConfigGroup = ["Wallpaper","org.kde.image","General"]; out = d[0].readConfig("Image", ""); } print(out);')"
    record @wallpaper - - "${old_wall:-$UNSET}"
  fi
  if (( DRY_RUN )); then
    info "Wallpaper → $wall"
  else
    plasma_eval "desktops().forEach(function (d) { d.wallpaperPlugin = 'org.kde.image'; d.currentConfigGroup = ['Wallpaper','org.kde.image','General']; d.writeConfig('Image', 'file://$wall'); });" >/dev/null
  fi
  ok "Glas-Wallpaper"

  # Pinned apps in the panel: hub first, then the essentials that exist here.
  local -a launchers=()
  local id
  for id in novadeck-hub.desktop steam.desktop com.google.Chrome.desktop org.kde.dolphin.desktop \
            org.kde.discover.desktop novadeck-keyboard.desktop systemsettings.desktop; do
    desktop_id_exists "$id" && launchers+=("applications:$id")
  done
  local js_list
  js_list="$(printf "'%s'," "${launchers[@]}")"
  js_list="[${js_list%,}]"

  local current line
  current="$(plasma_eval 'var out = []; panels().forEach(function (p) { p.widgets().forEach(function (w) { if (w.type == "org.kde.plasma.icontasks" || w.type == "org.kde.plasma.taskmanager") { w.currentConfigGroup = ["General"]; out.push(p.id + ":" + w.id + "=" + String(w.readConfig("launchers", ""))); } }); }); print(out.join("\n"));')"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    recorded @launchers "${line%%=*}" - || record @launchers "${line%%=*}" - "${line#*=}"
  done <<< "$current"

  if (( DRY_RUN )); then
    info "Leiste → ${launchers[*]}"
  else
    plasma_eval "panels().forEach(function (p) { p.widgets().forEach(function (w) { if (w.type == 'org.kde.plasma.icontasks' || w.type == 'org.kde.plasma.taskmanager') { w.currentConfigGroup = ['General']; w.writeConfig('launchers', $js_list); if (w.reloadConfig) { w.reloadConfig(); } } }); });" >/dev/null
  fi
  ok "Leiste aufgeräumt: NovaDeck, Steam, Browser, Dateien, App Store, Tastatur"
}

start_hub() {
  [[ -n "$PY" ]] || return 0
  (( DRY_RUN )) && return 0
  has_gui || return 0
  step "Los geht's"
  nohup "$PY" "$PREFIX/app/novadeck.py" hub >/dev/null 2>&1 &
  ok "NovaDeck startet …"
}

summary() {
  printf '\n%s  NovaDeck %s ist eingerichtet.%s\n\n' "$B$W" "$NOVA_VERSION" "$N"
  printf '  %sHub öffnen%s       Icon „NovaDeck“ unten in der Leiste oder auf dem Desktop\n' "$B" "$N"
  printf '  %sBedienen%s         Steuerkreuz, A = Starten, B = Zurück, L2 = Optionen\n' "$B" "$N"
  printf '  %sTastatur%s         Steam + X (oder Tastatur-Kachel im Hub)\n' "$B" "$N"
  printf '  %sEntfernen%s        App-Menü → „NovaDeck entfernen“\n' "$B" "$N"
  printf '                   oder: %s\n\n' "$PREFIX/uninstall.sh"
  (( WANT_THEME )) && printf '  %sTipp:%s Einmal in den Gaming-Modus und zurück wechseln, dann ist das Design überall aktiv.\n\n' "$D" "$N"
  return 0
}

main() {
  printf '\n%s  ✦  N O V A D E C K  %s%s\n' "$B$W" "$NOVA_VERSION" "$N"
  printf '  %sDein Steam-Deck-Desktop, aufgeräumt.%s\n' "$D" "$N"
  preflight
  choose
  install_files
  install_apps
  apply_theme
  start_hub
  summary
}

mkdir -p "$LOG_DIR" 2>/dev/null || true
if (( DRY_RUN )); then
  main
else
  main 2>&1 | tee "$LOG_DIR/setup.log"
  exit "${PIPESTATUS[0]}"
fi
