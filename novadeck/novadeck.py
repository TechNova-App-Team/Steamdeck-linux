#!/usr/bin/env python3
"""NovaDeck: a clean, controller-friendly hub for the Steam Deck desktop mode.

The hub is a small web UI that runs in an app window of your browser. This
script serves that UI from 127.0.0.1 only, launches apps, web apps and
system actions on request, and also offers a handful of CLI commands
(see `novadeck.py --help`).

Standard library only: no pip, no root, nothing that a SteamOS update can
break.
"""

from __future__ import annotations

import argparse
import errno
import http.server
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
CATALOG_FILE = APP_DIR / "catalog.json"
HOME = Path.home()


def _load_version_info() -> dict:
    try:
        with open(APP_DIR / "version.json", encoding="utf-8") as fh:
            info = json.load(fh)
        if isinstance(info, dict) and isinstance(info.get("version"), str):
            return info
    except (OSError, ValueError):
        pass
    return {"version": "0.0.0", "notes": []}


VERSION_INFO = _load_version_info()
VERSION = VERSION_INFO["version"]


def _xdg(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value and os.path.isabs(value) else default


CONFIG_HOME = _xdg("XDG_CONFIG_HOME", HOME / ".config")
DATA_HOME = _xdg("XDG_DATA_HOME", HOME / ".local" / "share")
CONFIG_DIR = CONFIG_HOME / "novadeck"
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local" / "state") / "novadeck"
CACHE_DIR = _xdg("XDG_CACHE_HOME", HOME / ".cache") / "novadeck"
_RUNTIME = os.environ.get("XDG_RUNTIME_DIR")
RUNTIME_DIR = Path(_RUNTIME) / "novadeck" if _RUNTIME else CACHE_DIR / "run"

STATE_FILE = CONFIG_DIR / "state.json"
AUTOSTART_FILE = CONFIG_HOME / "autostart" / "novadeck-hub.desktop"
APPLICATIONS_DIR = DATA_HOME / "applications"

HOST = "127.0.0.1"
PORT = int(os.environ.get("NOVADECK_PORT", "47820"))
DEMO = os.environ.get("NOVADECK_DEMO") == "1"
DEBUG = os.environ.get("NOVADECK_DEBUG") == "1"

HUB_PROFILE_DIRNAME = "novadeck-hub-profile"
FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"
CHROME_FLATPAK = "com.google.Chrome"

# Where NovaDeck updates itself from (the public GitHub repo, default branch).
UPDATE_REPO = os.environ.get("NOVADECK_REPO", "TechNova-App-Team/Steamdeck-linux")
UPDATE_REF = os.environ.get("NOVADECK_REF", "HEAD")
UPDATE_INTERVAL = 6 * 3600
CLOUD_GAMING_PAUSE = 3 * 3600  # no background downloads this long after starting a cloud game

# Browsers we know how to drive, in order of preference. Chromium-based ones
# give real app windows (no tabs, no address bar) and play Netflix & co.
BROWSERS = [
    ("com.google.Chrome", "Google Chrome", "flatpak", "chromium"),
    ("com.microsoft.Edge", "Microsoft Edge", "flatpak", "chromium"),
    ("com.brave.Browser", "Brave", "flatpak", "chromium"),
    ("org.chromium.Chromium", "Chromium", "flatpak", "chromium"),
    ("io.github.ungoogled_software.ungoogled_chromium", "Ungoogled Chromium", "flatpak", "chromium"),
    ("google-chrome-stable", "Google Chrome", "native", "chromium"),
    ("microsoft-edge-stable", "Microsoft Edge", "native", "chromium"),
    ("brave-browser", "Brave", "native", "chromium"),
    ("chromium", "Chromium", "native", "chromium"),
    ("org.mozilla.firefox", "Firefox", "flatpak", "firefox"),
    ("firefox", "Firefox", "native", "firefox"),
]
CHROMIUM_FLATPAKS = {b[0] for b in BROWSERS if b[2] == "flatpak" and b[3] == "chromium"}

CATEGORY_DESKTOP = {
    "play": "Game;",
    "watch": "AudioVideo;Video;",
    "social": "AudioVideo;Audio;Network;",
    "tools": "Utility;",
}

# Fake data so the UI can be previewed on any machine (NOVADECK_DEMO=1).
DEMO_FLATPAKS = {"com.google.Chrome", "org.prismlauncher.PrismLauncher", "com.spotify.Client"}
DEMO_SYSTEM = {
    "battery": {"percent": 82, "charging": False, "plugged": False, "status": "Discharging"},
    "network": {"type": "wifi", "connected": True, "name": "Nova-5G", "signal": 78},
    "volume": {"percent": 45, "muted": False},
    "brightness": {"percent": 70},
    "storage": [
        {"id": "internal", "label": "Intern", "total": 512_110_190_592, "free": 212_419_510_272},
        {"id": "sd", "label": "SD-Karte", "total": 1_000_204_886_016, "free": 402_653_184_000},
    ],
}


def log(msg: str) -> None:
    if DEBUG:
        sys.stderr.write(f"[novadeck] {msg}\n")


def run_out(argv: list[str], timeout: float = 3.0) -> str:
    """Run a small helper command and return its stdout ("" on any failure)."""
    try:
        res = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        return res.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def spawn(argv: list[str], log_name: str = "apps", env: dict | None = None) -> None:
    """Start a program detached from us, with its output in a log file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = STATE_DIR / f"{log_name}.log"
    try:
        if log_path.stat().st_size > 2_000_000:
            log_path.unlink()
    except OSError:
        pass
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "ab") as fh:
        fh.write(f"\n[{stamp}] $ {' '.join(argv)}\n".encode())
        fh.flush()
        if DEMO:
            fh.write(b"(demo mode: not started)\n")
            return
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True, env=env)
    # Reap the child when it exits so it never lingers as a zombie.
    threading.Thread(target=proc.wait, daemon=True).start()


def in_gaming_mode() -> bool:
    """True when we were started from the SteamOS Gaming Mode (gamescope)."""
    return (os.environ.get("XDG_CURRENT_DESKTOP", "").lower() == "gamescope"
            or bool(os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")))


def is_steamos() -> bool:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"^ID=\"?steamos\"?$", text, re.M))


def notify(title: str, body: str = "") -> None:
    if shutil.which("notify-send") and not DEMO:
        run_out(["notify-send", "-a", "NovaDeck", "-i", "novadeck", title, body], timeout=2)


# --------------------------------------------------------------------------
# Catalog & persistent state
# --------------------------------------------------------------------------

def load_catalog() -> dict:
    with open(CATALOG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


DEFAULT_SETTINGS = {"closeOnLaunch": True, "reducedEffects": False, "autoUpdate": True, "browser": None}
CUSTOM_CATEGORIES = {"play", "watch", "social", "tools"}


def valid_web_url(url: object) -> bool:
    if not isinstance(url, str) or len(url) > 2048 or any(c in url for c in "\r\n\t "):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        parts.port  # raises ValueError for nonsense ports
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(re.fullmatch(r"[A-Za-z0-9.\-]+|[0-9a-fA-F:]+", host))


def normalize_web_url(text: str) -> str:
    """Accepts "ardmediathek.de" as well as full URLs; other schemes stay invalid."""
    text = text.strip()
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):(?!\d)", text)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        return text
    return text if "://" in text else "https://" + text


def _valid_custom(item: object) -> bool:
    return (isinstance(item, dict)
            and isinstance(item.get("id"), str) and item["id"].startswith("custom-")
            and isinstance(item.get("name"), str) and 0 < len(item["name"]) <= 40
            and valid_web_url(item.get("url"))
            and item.get("category") in CUSTOM_CATEGORIES)


class Store:
    """The user's favourites, hidden tiles, custom tiles and settings."""

    def __init__(self, catalog: dict):
        self.lock = threading.RLock()
        self.catalog = catalog
        self.data = self._load()

    def _defaults(self) -> dict:
        return {
            "favorites": list(self.catalog.get("defaultFavorites", [])),
            "hidden": [],
            "custom": [],
            "settings": dict(DEFAULT_SETTINGS),
            "last": None,
            "seenVersion": VERSION,
        }

    def _load(self) -> dict:
        data = self._defaults()
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                saved = json.load(fh)
        except FileNotFoundError:
            return data
        except (OSError, ValueError):
            log("state file unreadable, using defaults")
            return data
        if not isinstance(saved, dict):
            return data
        for key in ("favorites", "hidden"):
            if isinstance(saved.get(key), list):
                data[key] = [x for x in saved[key] if isinstance(x, str)]
        if isinstance(saved.get("custom"), list):
            data["custom"] = [c for c in saved["custom"] if _valid_custom(c)]
        if isinstance(saved.get("settings"), dict):
            for key, value in saved["settings"].items():
                if key in DEFAULT_SETTINGS:
                    data["settings"][key] = value
        if isinstance(saved.get("last"), str):
            data["last"] = saved["last"]
        # Existing installs from before 1.1 did not track this: show them what's new.
        data["seenVersion"] = saved["seenVersion"] if isinstance(saved.get("seenVersion"), str) else "1.0.0"
        return data

    def save(self) -> None:
        with self.lock:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, STATE_FILE)

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.data))

    def update(self, fn) -> dict:
        with self.lock:
            fn(self.data)
            self.save()
            return self.snapshot()


def all_apps(catalog: dict, state: dict) -> list[dict]:
    apps = [dict(a) for a in catalog["apps"]]
    for c in state.get("custom", []):
        host = urllib.parse.urlsplit(c["url"]).netloc.removeprefix("www.")
        apps.append({
            "id": c["id"], "name": c["name"], "subtitle": f"Eigene Kachel · {host}",
            "category": c["category"], "icon": c.get("icon") or "globe", "kind": "web",
            "url": c["url"], "custom": True,
            "info": f"Deine eigene Web-App für {host}. Öffnet sich als App-Fenster.",
        })
    return apps


def find_app(catalog: dict, state: dict, app_id: str) -> dict | None:
    return next((a for a in all_apps(catalog, state) if a["id"] == app_id), None)


# --------------------------------------------------------------------------
# Flatpak & browsers
# --------------------------------------------------------------------------

class FlatpakIndex:
    """Cached list of installed Flatpak apps (user and system)."""

    def __init__(self):
        self.lock = threading.Lock()
        self._at = -1e9
        self._ids: set[str] = set()

    @staticmethod
    def available() -> bool:
        return DEMO or shutil.which("flatpak") is not None

    def installed(self, max_age: float = 8.0) -> set[str]:
        if DEMO:
            return set(DEMO_FLATPAKS)
        with self.lock:
            if time.monotonic() - self._at < max_age:
                return set(self._ids)
            ids: set[str] = set()
            if shutil.which("flatpak"):
                out = run_out(["flatpak", "list", "--app", "--columns=application"], timeout=10)
                ids = {line.strip() for line in out.splitlines() if line.strip()}
            self._ids, self._at = ids, time.monotonic()
            return set(ids)

    def invalidate(self) -> None:
        with self.lock:
            self._at = -1e9


class Browser:
    def __init__(self, bid: str, name: str, kind: str, family: str):
        self.id, self.name, self.kind, self.family = bid, name, kind, family

    @property
    def base(self) -> list[str]:
        return ["flatpak", "run", self.id] if self.kind == "flatpak" else [self.id]

    def profile_dir(self) -> Path:
        # Inside a Flatpak sandbox only ~/.var/app/<id> is guaranteed writable.
        root = HOME / ".var" / "app" / self.id if self.kind == "flatpak" else CACHE_DIR
        return root / HUB_PROFILE_DIRNAME

    def hub_command(self, url: str) -> list[str]:
        profile = self.profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        if self.family == "firefox":
            return [*self.base, "--new-instance", "--profile", str(profile), "--kiosk", url]
        return [
            *self.base,
            f"--user-data-dir={profile}",
            f"--app={url}",
            "--start-fullscreen",
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--disable-sync",
            "--disable-pinch",
            "--overscroll-history-navigation=0",
            "--hide-crash-restore-bubble",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=Translate,MediaRouter",
            "--class=NovaDeck",
        ]

    def webapp_command(self, url: str, gaming: bool) -> list[str]:
        if self.family == "firefox":
            return [*self.base, "--kiosk", url] if gaming else [*self.base, "--new-window", url]
        if gaming:
            # Gaming Mode: full screen, bigger text for the 7" screen.
            return [*self.base, "--kiosk", "--start-fullscreen", "--force-device-scale-factor=1.25", url]
        return [*self.base, f"--app={url}", "--start-maximized"]

    def window_command(self) -> list[str]:
        return [*self.base, "--new-window"]

    def public(self) -> dict:
        return {"id": self.id, "name": self.name}


def available_browsers(flatpaks: set[str]) -> list[Browser]:
    found = []
    for bid, name, kind, family in BROWSERS:
        native_ok = kind == "native" and not DEMO and shutil.which(bid)
        if (kind == "flatpak" and bid in flatpaks) or native_ok:
            found.append(Browser(bid, name, kind, family))
    names = [b.name for b in found]
    for b in found:  # same browser twice (Flatpak + system package): tell them apart
        if names.count(b.name) > 1:
            b.name += " (Flatpak)" if b.kind == "flatpak" else " (System)"
    return found


def pick_browser(settings: dict, flatpaks: set[str]) -> Browser | None:
    found = available_browsers(flatpaks)
    preferred = settings.get("browser")
    for b in found:
        if b.id == preferred:
            return b
    return found[0] if found else None


# --------------------------------------------------------------------------
# Launching
# --------------------------------------------------------------------------

class LaunchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def describe_app(app: dict, flatpaks: set[str], browser: Browser | None) -> dict:
    kind = app["kind"]
    available, install_id, reason = True, None, ""
    if kind == "flatpak":
        available = app["flatpak"] in flatpaks
        if not available:
            reason = "Noch nicht installiert"
            install_id = app["flatpak"] if FlatpakIndex.available() else None
    elif kind in ("web", "browser"):
        available = browser is not None
        if not available:
            reason = "Braucht einen Browser"
            install_id = CHROME_FLATPAK if FlatpakIndex.available() else None
    elif kind == "command":
        available = DEMO or shutil.which(app["command"][0]) is not None
        reason = "" if available else "Auf diesem System nicht vorhanden"
    elif kind == "updates":
        available = True
    elif kind == "uri":
        req = app.get("requires")
        available = DEMO or (shutil.which("xdg-open") is not None and (not req or shutil.which(req) is not None))
        reason = "" if available else "Steam läuft nicht"
    public = {k: app.get(k) for k in ("id", "name", "subtitle", "category", "icon", "kind", "info", "flatpak", "url")}
    public["steamName"] = app.get("steamName") or app["name"]
    public.update({
        "custom": bool(app.get("custom")),
        "available": available,
        "installId": install_id,
        "reason": reason,
        "keepHub": bool(app.get("keepHub")) or app["id"] == "keyboard" or kind == "updates",
    })
    return public


def launch_argv(app: dict, settings: dict, flatpaks: set[str], gaming: bool) -> list[str]:
    kind = app["kind"]
    if kind == "flatpak":
        if app["flatpak"] not in flatpaks:
            raise LaunchError("not_installed", f"{app['name']} ist noch nicht installiert.")
        return ["flatpak", "run", app["flatpak"]]
    if kind in ("web", "browser"):
        browser = pick_browser(settings, flatpaks)
        if browser is None:
            raise LaunchError("no_browser", "Kein Browser gefunden. Installiere Google Chrome über den App Store.")
        return browser.webapp_command(app["url"], gaming) if kind == "web" else browser.window_command()
    if kind == "command":
        exe = shutil.which(app["command"][0])
        if not exe and not DEMO:
            raise LaunchError("missing", f"{app['name']} ist auf diesem System nicht vorhanden.")
        return [exe or app["command"][0], *app["command"][1:]]
    if kind == "uri":
        return ["xdg-open", app["uri"]]
    raise LaunchError("unknown", "Unbekannter App-Typ.")


# --------------------------------------------------------------------------
# Install jobs (Flatpak from Flathub, per-user, no password needed)
# --------------------------------------------------------------------------

class Jobs:
    def __init__(self, index: FlatpakIndex):
        self.index = index
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}

    def snapshot(self) -> dict:
        with self.lock:
            return {k: dict(v) for k, v in self.items.items()}

    def start(self, flatpak_id: str, label: str) -> dict:
        with self.lock:
            job = self.items.get(flatpak_id)
            if job and job["status"] == "running":
                return dict(job)
            job = {"status": "running", "label": label, "started": time.time(), "message": ""}
            self.items[flatpak_id] = job
        threading.Thread(target=self._run, args=(flatpak_id,), daemon=True).start()
        return dict(job)

    def _finish(self, flatpak_id: str, ok: bool, message: str = "") -> None:
        with self.lock:
            self.items[flatpak_id].update(status="done" if ok else "error", message=message, ended=time.time())
        self.index.invalidate()

    def _run(self, flatpak_id: str) -> None:
        if DEMO:
            time.sleep(4)
            DEMO_FLATPAKS.add(flatpak_id)
            return self._finish(flatpak_id, True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "install.log", "ab") as fh:
            def step(argv: list[str], timeout: float) -> int:
                fh.write(f"\n$ {' '.join(argv)}\n".encode())
                fh.flush()
                try:
                    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                          timeout=timeout, check=False).returncode
                except (OSError, subprocess.SubprocessError) as exc:
                    fh.write(f"{exc}\n".encode())
                    return 1

            step(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO], 120)
            code = step(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", flatpak_id], 3600)
            if code == 0 and flatpak_id in CHROMIUM_FLATPAKS:
                # Lets the browser see controllers (cloud gaming); Valve does the same for Chrome.
                step(["flatpak", "override", "--user", "--filesystem=/run/udev:ro", flatpak_id], 30)
        if code == 0:
            self._finish(flatpak_id, True)
        else:
            self._finish(flatpak_id, False, "Installation fehlgeschlagen. Bist du mit dem Internet verbunden?")


# --------------------------------------------------------------------------
# System status & actions
# --------------------------------------------------------------------------

def battery_status() -> dict | None:
    base = Path("/sys/class/power_supply")
    if not base.is_dir():
        return None
    for dev in sorted(base.iterdir()):
        try:
            if (dev / "type").read_text().strip() != "Battery":
                continue
            percent = int((dev / "capacity").read_text().strip())
            status = (dev / "status").read_text().strip()
        except (OSError, ValueError):
            continue
        return {"percent": max(0, min(100, percent)), "charging": status == "Charging",
                "plugged": status in ("Charging", "Full", "Not charging"), "status": status}
    return None


def _split_nmcli(line: str) -> list[str]:
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in re.split(r"(?<!\\):", line)]


def network_status() -> dict | None:
    if not shutil.which("nmcli"):
        return None
    out = run_out(["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"], 2)
    wifi_name, ethernet = None, False
    for line in out.splitlines():
        parts = _split_nmcli(line)
        if len(parts) < 3 or parts[1] != "connected":
            continue
        if parts[0] == "ethernet":
            ethernet = True
        elif parts[0] == "wifi":
            wifi_name = parts[2]
    if ethernet:
        return {"type": "ethernet", "connected": True, "name": "LAN"}
    if wifi_name is None:
        return {"type": "none", "connected": False}
    signal_strength = None
    out = run_out(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no"], 3)
    for line in out.splitlines():
        parts = _split_nmcli(line)
        if len(parts) >= 3 and parts[0] == "yes":
            wifi_name = parts[1] or wifi_name
            signal_strength = int(parts[2]) if parts[2].isdigit() else None
            break
    return {"type": "wifi", "connected": True, "name": wifi_name, "signal": signal_strength}


def volume_get() -> dict | None:
    if DEMO:
        return dict(DEMO_SYSTEM["volume"])
    if shutil.which("wpctl"):
        out = run_out(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        m = re.search(r"Volume:\s*([0-9.]+)", out)
        if m:
            return {"percent": round(float(m.group(1)) * 100), "muted": "[MUTED]" in out}
    if shutil.which("pactl"):
        out = run_out(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", out)
        if m:
            mute = run_out(["pactl", "get-sink-mute", "@DEFAULT_SINK@"]).lower()
            return {"percent": int(m.group(1)), "muted": "yes" in mute or "ja" in mute}
    return None


def volume_set(percent: int | None = None, mute: str | None = None) -> None:
    if DEMO:
        if percent is not None:
            DEMO_SYSTEM["volume"].update(percent=percent, muted=False)
        if mute == "toggle":
            DEMO_SYSTEM["volume"]["muted"] = not DEMO_SYSTEM["volume"]["muted"]
        return
    if shutil.which("wpctl"):
        if percent is not None:
            run_out(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{percent / 100:.2f}"])
            run_out(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
        if mute == "toggle":
            run_out(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
    elif shutil.which("pactl"):
        if percent is not None:
            run_out(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])
            run_out(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
        if mute == "toggle":
            run_out(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])


class Brightness:
    """Screen brightness via Plasma 6, Plasma 5 or a writable sysfs backlight."""

    def __init__(self):
        self.backend: str | None = None
        self.display = "display0"
        self.sysfs: Path | None = None
        self._detected = False

    @staticmethod
    def _dbus(*args: str) -> str:
        return run_out(["dbus-send", "--session", "--print-reply=literal", *args], 2)

    @staticmethod
    def _last_int(text: str) -> int | None:
        nums = re.findall(r"-?\d+", text)
        return int(nums[-1]) if nums else None

    def detect(self) -> str | None:
        if self._detected:
            return self.backend
        self._detected = True
        if DEMO:
            self.backend = "demo"
            return self.backend
        if shutil.which("dbus-send"):
            out = self._dbus("--dest=org.kde.ScreenBrightness", "/org/kde/ScreenBrightness",
                             "org.freedesktop.DBus.Properties.Get", "string:org.kde.ScreenBrightness",
                             "string:DisplaysDBusNames")
            names = re.findall(r"\b(display\d+)\b", out)
            if names:
                self.display, self.backend = names[0], "kde6"
                return self.backend
            out = self._dbus("--dest=org.kde.Solid.PowerManagement",
                             "/org/kde/Solid/PowerManagement/Actions/BrightnessControl",
                             "org.kde.Solid.PowerManagement.Actions.BrightnessControl.brightnessMax")
            if self._last_int(out):
                self.backend = "kde5"
                return self.backend
        backlight = Path("/sys/class/backlight")
        if backlight.is_dir():
            for dev in sorted(backlight.iterdir()):
                if os.access(dev / "brightness", os.W_OK):
                    self.sysfs, self.backend = dev, "sysfs"
                    return self.backend
        return None

    def _kde6_prop(self, name: str) -> int | None:
        return self._last_int(self._dbus("--dest=org.kde.ScreenBrightness", f"/org/kde/ScreenBrightness/{self.display}",
                                         "org.freedesktop.DBus.Properties.Get",
                                         "string:org.kde.ScreenBrightness.Display", f"string:{name}"))

    def _kde5(self, method: str, *args: str) -> int | None:
        return self._last_int(self._dbus("--dest=org.kde.Solid.PowerManagement",
                                          "/org/kde/Solid/PowerManagement/Actions/BrightnessControl",
                                          f"org.kde.Solid.PowerManagement.Actions.BrightnessControl.{method}", *args))

    def _range(self) -> tuple[int | None, int | None]:
        backend = self.detect()
        if backend == "kde6":
            return self._kde6_prop("Brightness"), self._kde6_prop("MaxBrightness")
        if backend == "kde5":
            return self._kde5("brightness"), self._kde5("brightnessMax")
        if backend == "sysfs" and self.sysfs:
            try:
                return (int((self.sysfs / "brightness").read_text()), int((self.sysfs / "max_brightness").read_text()))
            except (OSError, ValueError):
                pass
        return None, None

    def get(self) -> int | None:
        if self.detect() == "demo":
            return DEMO_SYSTEM["brightness"]["percent"]
        value, maximum = self._range()
        if value is None or not maximum:
            return None
        return round(value * 100 / maximum)

    def set(self, percent: int) -> None:
        percent = max(5, min(100, int(percent)))
        backend = self.detect()
        if backend == "demo":
            DEMO_SYSTEM["brightness"]["percent"] = percent
            return
        _, maximum = self._range()
        if not maximum:
            return
        value = max(1, round(maximum * percent / 100))
        if backend == "kde6":
            run_out(["dbus-send", "--session", "--type=method_call", "--dest=org.kde.ScreenBrightness",
                     f"/org/kde/ScreenBrightness/{self.display}", "org.kde.ScreenBrightness.Display.SetBrightness",
                     f"int32:{value}", "uint32:0"], 2)
        elif backend == "kde5":
            self._kde5("setBrightness", f"int32:{value}")
        elif backend == "sysfs" and self.sysfs:
            try:
                (self.sysfs / "brightness").write_text(str(value))
            except OSError:
                pass


def storage_status() -> list[dict]:
    items = []
    try:
        du = shutil.disk_usage(HOME)
        items.append({"id": "internal", "label": "Intern", "total": du.total, "free": du.free})
    except OSError:
        pass
    seen = set()
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            mounts = [line.split()[:2] for line in fh if line.strip()]
    except OSError:
        mounts = []
    for dev, mnt in mounts:
        mnt = mnt.replace("\\040", " ")
        if dev in seen:
            continue
        if dev.startswith("/dev/mmcblk"):
            label, kind = "SD-Karte", "sd"
        elif dev.startswith("/dev/sd") and mnt.startswith("/run/media/"):
            label, kind = Path(mnt).name or "USB", "usb"
        else:
            continue
        try:
            du = shutil.disk_usage(mnt)
        except OSError:
            continue
        seen.add(dev)
        items.append({"id": kind, "label": label, "total": du.total, "free": du.free})
    return items


def to_gaming_mode() -> None:
    if DEMO:
        return spawn(["steamos-session-select", "gamescope"], "system")
    if shutil.which("steamos-session-select"):
        return spawn(["steamos-session-select", "gamescope"], "system")
    # Any other KDE system: just log out of the desktop session.
    spawn(["sh", "-c",
           "dbus-send --session --type=method_call --dest=org.kde.Shutdown /Shutdown org.kde.Shutdown.logout"
           " || dbus-send --session --type=method_call --dest=org.kde.ksmserver /KSMServer"
           " org.kde.KSMServerInterface.logout int32:0 int32:0 int32:0"], "system")


POWER_COMMANDS = {
    "suspend": ["systemctl", "suspend"],
    "reboot": ["systemctl", "reboot"],
    "poweroff": ["systemctl", "poweroff"],
}


# --------------------------------------------------------------------------
# Hub window handling
# --------------------------------------------------------------------------

def hub_ui_pids() -> list[int]:
    mark = HUB_PROFILE_DIRNAME.encode()
    me = os.getpid()
    pids = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids
    for name in entries:
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                cmd = fh.read()
        except OSError:
            continue
        if mark in cmd and b"novadeck.py" not in cmd:
            pids.append(int(name))
    return pids


def close_hub_ui(wait: float = 2.0) -> bool:
    pids = hub_ui_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + wait
    while pids and time.monotonic() < deadline:
        time.sleep(0.1)
        pids = hub_ui_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return True


def open_hub_ui(store: Store, index: FlatpakIndex) -> str:
    close_hub_ui()
    url = f"http://{HOST}:{PORT}/"
    browser = pick_browser(store.snapshot()["settings"], index.installed())
    if browser is None:
        spawn(["xdg-open", url], "hub")
        return "xdg-open"
    spawn(browser.hub_command(url), "hub")
    return browser.name


# --------------------------------------------------------------------------
# .desktop files (app menu, autostart; also usable as non-Steam games)
# --------------------------------------------------------------------------

def _exec_arg(arg: str) -> str:
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[A-Za-z0-9_@+=:,./-]+", arg):
        return arg
    return '"' + "".join("\\\\" + c if c in '"`$\\' else c for c in arg) + '"'


def _exec_line(*args: str) -> str:
    return " ".join(_exec_arg(a) for a in (sys.executable or "/usr/bin/python3", str(Path(__file__).resolve()), *args))


def _desktop_entry(name: str, comment: str, exec_line: str, icon: str, categories: str,
                   keywords: str = "", extra: dict | None = None) -> str:
    lines = [
        "[Desktop Entry]", "Type=Application", "Version=1.0",
        f"Name={name}", f"Comment={comment}", f"Exec={exec_line}", f"Icon={icon}",
        "Terminal=false", f"Categories={categories}", "StartupNotify=false", "X-NovaDeck=true",
    ]
    if keywords:
        lines.append(f"Keywords={keywords}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding="utf-8") == content:
            return
    except OSError:
        pass
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def hub_desktop_entry(autostart: bool = False) -> str:
    extra = {"X-KDE-autostart-after": "panel", "X-GNOME-Autostart-enabled": "true"} if autostart else None
    return _desktop_entry(
        "NovaDeck", "Dein Start-Bildschirm für den Desktop-Modus",
        _exec_line("hub", "--autostart") if autostart else _exec_line("hub"),
        "novadeck", "Game;Utility;", "hub;launcher;start;steam deck;", extra)


def write_desktop_files(catalog: dict, state: dict) -> list[Path]:
    wanted: dict[Path, str] = {
        APPLICATIONS_DIR / "novadeck-hub.desktop": hub_desktop_entry(),
        APPLICATIONS_DIR / "novadeck-keyboard.desktop": _desktop_entry(
            "Bildschirmtastatur", "Steam-Tastatur einblenden (auch: Steam + X)",
            _exec_line("launch", "keyboard"), "novadeck-keyboard", "Utility;Accessibility;", "keyboard;tastatur;"),
    }
    for app in all_apps(catalog, state):
        if app["kind"] != "web":
            continue
        wanted[APPLICATIONS_DIR / f"novadeck-{app['id']}.desktop"] = _desktop_entry(
            app["name"], f"{app['subtitle']} · NovaDeck", _exec_line("launch", app["id"]),
            f"novadeck-{app.get('icon') or 'globe'}", CATEGORY_DESKTOP.get(app["category"], "Utility;"))
    for path, content in wanted.items():
        _write_if_changed(path, content)
    # Remove launchers we created earlier that are gone now (e.g. deleted custom tiles).
    if APPLICATIONS_DIR.is_dir():
        for path in APPLICATIONS_DIR.glob("novadeck-*.desktop"):
            if path not in wanted:
                try:
                    if "X-NovaDeck=true" in path.read_text(encoding="utf-8"):
                        path.unlink()
                except OSError:
                    pass
    return list(wanted)


def remove_desktop_files() -> None:
    for folder in (APPLICATIONS_DIR, AUTOSTART_FILE.parent):
        if not folder.is_dir():
            continue
        for path in folder.glob("novadeck-*.desktop"):
            try:
                if "X-NovaDeck=true" in path.read_text(encoding="utf-8"):
                    path.unlink()
            except OSError:
                pass


def set_autostart(enabled: bool) -> None:
    if enabled:
        _write_if_changed(AUTOSTART_FILE, hub_desktop_entry(autostart=True))
    else:
        try:
            AUTOSTART_FILE.unlink()
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------------
# Updates: Flatpak apps and NovaDeck itself
# --------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:3]) or (0,)


def fetch_remote_version(timeout: float = 6.0) -> dict | None:
    """Reads novadeck/version.json from the GitHub repo (None when offline)."""
    url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_REF}/novadeck/version.json"
    req = urllib.request.Request(url, headers={"User-Agent": f"NovaDeck/{VERSION}", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            info = json.loads(res.read(65536))
    except Exception:  # noqa: BLE001 - offline, GitHub down, bad JSON: all mean "unknown"
        return None
    if isinstance(info, dict) and isinstance(info.get("version"), str):
        notes = info.get("notes") if isinstance(info.get("notes"), list) else []
        return {"version": info["version"], "notes": [str(n) for n in notes][:12]}
    return None


def safe_extract(archive: Path, dest: Path) -> Path:
    """Unpacks a GitHub tarball without letting it write outside `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise ValueError(f"Unsicherer Pfad im Update: {member.name}")
            if member.isfile() or member.isdir():
                members.append(member)
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, members=members, filter="data")
        else:  # pragma: no cover - Python < 3.11.4
            tar.extractall(dest, members=members)
    for root in sorted(p for p in dest.iterdir() if p.is_dir()):
        if (root / "install.sh").is_file() and (root / "novadeck" / "novadeck.py").is_file():
            return root
    raise ValueError("Update-Paket ist unvollständig.")


def apply_self_update(reopen: bool) -> None:
    """Downloads the newest NovaDeck and runs its installer in update mode (detached)."""
    if DEMO:
        return spawn(["true"], "update")
    work = CACHE_DIR / "update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "novadeck.tar.gz"
    url = f"https://codeload.github.com/{UPDATE_REPO}/tar.gz/{UPDATE_REF}"
    req = urllib.request.Request(url, headers={"User-Agent": f"NovaDeck/{VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as res, open(archive, "wb") as fh:
        copied = 0
        while chunk := res.read(1 << 16):
            copied += len(chunk)
            if copied > 200 * 1024 * 1024:
                raise ValueError("Update-Paket ist zu groß.")
            fh.write(chunk)
    src = safe_extract(archive, work / "src")
    argv = ["bash", str(src / "install.sh"), "--update"] + (["--reopen"] if reopen else [])
    spawn(argv, "update", env={**os.environ, "NOVA_IN_TERMINAL": "1"})


def flatpak_pending_updates() -> tuple[list[dict], int, bool]:
    """Returns (apps with updates, number of other refs, whether the lookup worked)."""
    names: dict[str, str] = {}
    for line in run_out(["flatpak", "list", "--app", "--columns=application,name"], 15).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            names[parts[0].strip()] = parts[1].strip() or parts[0].strip()
    apps: list[dict] = []
    others, seen, worked = 0, set(), False
    for scope in ("--user", "--system"):
        try:
            res = subprocess.run(["flatpak", "remote-ls", scope, "--updates", "--columns=application"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=90, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        worked = worked or res.returncode == 0
        for ref in res.stdout.decode("utf-8", "replace").split():
            if ref in seen or ref.lower() == "application":
                continue
            seen.add(ref)
            if ref in names:
                apps.append({"id": ref, "name": names[ref], "scope": scope[2:]})
            else:
                others += 1
    return apps, others, worked


def system_updates_allowed() -> bool:
    """True if polkit lets us update system-wide Flatpaks without asking for a password."""
    if not shutil.which("pkcheck"):
        return False
    try:
        return subprocess.run(["pkcheck", "--action-id", "org.freedesktop.Flatpak.app-update",
                               "--process", str(os.getpid())], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _low_priority() -> list[str]:
    prefix = []
    if shutil.which("nice"):
        prefix += ["nice", "-n", "15"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    return prefix


class Updater:
    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.apps: list[dict] = []
        self.others = 0
        self.remote: dict | None = None
        self.checked_at: float | None = None
        self.checking = False
        self.running: dict | None = None
        self.last: dict | None = None
        self.error = ""
        self._demo_done = False

    def summary(self) -> dict:
        with self.lock:
            latest = (self.remote or {}).get("version") or VERSION
            self_available = parse_version(latest) > parse_version(VERSION)
            return {
                "apps": [dict(a) for a in self.apps],
                "others": self.others,
                "count": len(self.apps) + self.others + (1 if self_available else 0),
                "self": {"current": VERSION, "latest": latest, "available": self_available,
                         "notes": list((self.remote or {}).get("notes", [])) if self_available else []},
                "checking": self.checking,
                "checkedAt": self.checked_at,
                "running": dict(self.running) if self.running else None,
                "last": dict(self.last) if self.last else None,
                "error": self.error,
            }

    def check_async(self) -> None:
        threading.Thread(target=self.check, daemon=True).start()

    def check(self) -> None:
        with self.lock:
            if self.checking or self.running:
                return
            self.checking = True
        try:
            if DEMO:
                time.sleep(1.0)
                pending = not self._demo_done
                apps = [{"id": "com.google.Chrome", "name": "Google Chrome", "scope": "user"}] if pending else []
                others, worked = (2 if pending else 0), True
                remote = {"version": "1.2.0" if pending else VERSION,
                          "notes": ["Beispiel: Neue Funktion aus der Demo."] if pending else []}
            else:
                apps, others, worked = flatpak_pending_updates() if shutil.which("flatpak") else ([], 0, True)
                remote = fetch_remote_version()
            with self.lock:
                self.apps, self.others = apps, others
                if remote:
                    self.remote = remote
                self.checked_at = time.time()
                self.error = "" if worked or remote else "Keine Verbindung. Bist du online?"
        finally:
            with self.lock:
                self.checking = False

    def run(self, auto: bool = False, include_self: bool = True) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = {"step": "Starte …", "started": time.time(), "auto": auto, "selfUpdate": False}
        threading.Thread(target=self._run, args=(auto, include_self), daemon=True).start()
        return True

    def _step(self, text: str) -> None:
        with self.lock:
            if self.running:
                self.running["step"] = text

    def _stream(self, argv: list[str], names: dict[str, str]) -> int:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "update.log", "ab") as logfh:
            logfh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] $ {' '.join(argv)}\n".encode())
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
            except OSError as exc:
                logfh.write(f"{exc}\n".encode())
                return 1
            for raw in proc.stdout:  # type: ignore[union-attr]
                logfh.write(raw)
                m = re.search(r"(?:app|runtime)/([A-Za-z0-9_.\-]+)/", raw.decode("utf-8", "replace"))
                if m:
                    self._step(f"Aktualisiere {names.get(m.group(1), m.group(1))} …")
            return proc.wait()

    def _run(self, auto: bool, include_self: bool) -> None:
        ok, updated, handed_over = True, 0, False
        try:
            before = self.summary()
            updated = len(before["apps"]) + before["others"]
            if DEMO:
                for name in ("Google Chrome", "Laufzeitumgebung"):
                    self._step(f"Aktualisiere {name} …")
                    time.sleep(1.5)
                self._demo_done = True
            elif shutil.which("flatpak"):
                names = {a["id"]: a["name"] for a in before["apps"]}
                scopes = ["--user"]
                if not auto or system_updates_allowed():
                    scopes.append("--system")
                prefix = _low_priority() if auto else []
                for scope in scopes:
                    self._step("Suche nach App-Updates …")
                    if self._stream(prefix + ["flatpak", "update", scope, "-y", "--noninteractive"], names) != 0:
                        ok = False
                    # Leftover runtimes; failures here (e.g. "can't remove …") are harmless.
                    self._step("Räume auf …")
                    run_out(prefix + ["flatpak", "uninstall", scope, "--unused", "-y", "--noninteractive"], 900)
                self.ctx.flatpaks.invalidate()
            if include_self and before["self"]["available"]:
                self._step(f"Lade NovaDeck {before['self']['latest']} …")
                if DEMO:
                    time.sleep(1.2)
                else:
                    with self.lock:
                        if self.running:
                            self.running["selfUpdate"] = True
                    apply_self_update(reopen=bool(hub_ui_pids()) or not auto)
                    handed_over = True
                    return  # the installer takes over and restarts NovaDeck
        except Exception as exc:  # noqa: BLE001
            log(f"update failed: {exc!r}")
            ok = False
        finally:
            with self.lock:
                if not handed_over:
                    self.running = None
                    if ok:
                        message = "Alles aktualisiert." if updated else "Alles war schon aktuell."
                    else:
                        message = "Nicht alles ließ sich aktualisieren. Später nochmal versuchen."
                    self.last = {"ok": ok, "message": message, "at": time.time(), "auto": auto, "count": updated}
        self.check()

    def auto_allowed(self) -> bool:
        if time.time() - self.ctx.last_cloud_launch < CLOUD_GAMING_PAUSE:
            return False
        bat = battery_status()
        return bat is None or bat["plugged"] or bat["percent"] >= 30

    def background(self) -> None:
        time.sleep(25)
        while True:
            self.check()
            settings = self.ctx.store.snapshot()["settings"]
            pending = self.summary()
            if (settings.get("autoUpdate") and not DEMO and self.auto_allowed()
                    and (pending["apps"] or pending["others"])):
                self.run(auto=True, include_self=False)
            time.sleep(UPDATE_INTERVAL)


# --------------------------------------------------------------------------
# HTTP API
# --------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message


class Context:
    def __init__(self, token: str):
        self.token = token
        self.catalog = load_catalog()
        self.store = Store(self.catalog)
        self.flatpaks = FlatpakIndex()
        self.jobs = Jobs(self.flatpaks)
        self.brightness = Brightness()
        self.updater = Updater(self)
        self.last_cloud_launch = 0.0
        self._status_cache: tuple[float, dict] = (-1e9, {})
        self._status_lock = threading.Lock()
        self.shutdown_cb = None
        self.routes = {
            "/api/launch": self.api_launch,
            "/api/install": self.api_install,
            "/api/favorite": self.api_favorite,
            "/api/hide": self.api_hide,
            "/api/unhide-all": self.api_unhide_all,
            "/api/custom/add": self.api_custom_add,
            "/api/custom/remove": self.api_custom_remove,
            "/api/settings": self.api_settings,
            "/api/volume": self.api_volume,
            "/api/brightness": self.api_brightness,
            "/api/power": self.api_power,
            "/api/hub/close": self.api_hub_close,
            "/api/steam/add": self.api_steam_add,
            "/api/updates/check": self.api_updates_check,
            "/api/updates/run": self.api_updates_run,
            "/api/whatsnew/seen": self.api_whatsnew_seen,
            "/api/quit": self.api_quit,
        }

    # -- read ---------------------------------------------------------------
    def full_state(self) -> dict:
        flatpaks = self.flatpaks.installed()
        state = self.store.snapshot()
        browser = pick_browser(state["settings"], flatpaks)
        apps = [describe_app(a, flatpaks, browser) for a in all_apps(self.catalog, state)]
        steamos = is_steamos() or DEMO
        return {
            "version": VERSION,
            "demo": DEMO,
            "steamos": steamos,
            "categories": self.catalog["categories"],
            "apps": apps,
            "favorites": state["favorites"],
            "hidden": state["hidden"],
            "last": state["last"],
            "settings": {**state["settings"], "autostart": AUTOSTART_FILE.exists()},
            "browser": browser.public() if browser else None,
            "browsers": [b.public() for b in available_browsers(flatpaks)],
            "jobs": self.jobs.snapshot(),
            "updates": self.updater.summary(),
            "whatsNew": VERSION_INFO if parse_version(state["seenVersion"]) < parse_version(VERSION) else None,
            "capabilities": {
                "gamemode": DEMO or bool(shutil.which("steamos-session-select") or shutil.which("dbus-send")),
                "gamemodeLabel": "Gaming-Modus" if steamos else "Abmelden",
                "power": DEMO or bool(shutil.which("systemctl")),
                "flatpak": FlatpakIndex.available(),
                "steam": DEMO or bool(shutil.which("steam") and shutil.which("xdg-open")),
            },
        }

    def status(self) -> dict:
        with self._status_lock:
            at, result = self._status_cache
            if time.monotonic() - at >= 2.5:
                result = self._read_system()
                self._status_cache = (time.monotonic(), result)
        result = dict(result)
        result["jobs"] = self.jobs.snapshot()
        result["updates"] = self.updater.summary()
        return result

    def _read_system(self) -> dict:
        if DEMO:
            result = {k: (json.loads(json.dumps(v))) for k, v in DEMO_SYSTEM.items()}
        else:
            result = {
                "battery": battery_status(),
                "network": network_status(),
                "volume": volume_get(),
                "brightness": None,
                "storage": storage_status(),
            }
            level = self.brightness.get()
            result["brightness"] = {"percent": level} if level is not None else None
        return result

    def _invalidate_status(self) -> None:
        with self._status_lock:
            self._status_cache = (-1e9, {})

    # -- helpers -------------------------------------------------------------
    def _app(self, body: dict) -> dict:
        app_id = body.get("id")
        if not isinstance(app_id, str):
            raise ApiError(400, "bad_request", "id fehlt")
        app = find_app(self.catalog, self.store.snapshot(), app_id)
        if not app:
            raise ApiError(404, "unknown_app", "Diese Kachel gibt es nicht mehr.")
        return app

    def _refresh_launchers(self) -> None:
        try:
            write_desktop_files(self.catalog, self.store.snapshot())
        except OSError as exc:
            log(f"desktop files: {exc}")

    # -- write ---------------------------------------------------------------
    def api_launch(self, body: dict) -> dict:
        app = self._app(body)
        state = self.store.snapshot()
        try:
            argv = launch_argv(app, state["settings"], self.flatpaks.installed(), gaming=False)
        except LaunchError as exc:
            raise ApiError(409, exc.code, exc.message) from exc
        spawn(argv)
        if app["category"] == "play" and app["kind"] == "web":
            self.last_cloud_launch = time.time()
        keep = bool(app.get("keepHub")) or app["id"] == "keyboard"
        if not keep:
            self.store.update(lambda d: d.__setitem__("last", app["id"]))
        closing = bool(state["settings"].get("closeOnLaunch")) and not keep and not DEMO
        if closing:
            threading.Timer(1.8, close_hub_ui).start()
        return {"closing": closing}

    def api_install(self, body: dict) -> dict:
        app = self._app(body)
        described = describe_app(app, self.flatpaks.installed(), None)
        flatpak_id = app.get("flatpak") if app["kind"] == "flatpak" else described.get("installId")
        if not flatpak_id:
            raise ApiError(409, "not_installable", "Das lässt sich hier nicht installieren.")
        if not FlatpakIndex.available():
            raise ApiError(409, "no_flatpak", "Flatpak ist auf diesem System nicht verfügbar.")
        label = app["name"] if app["kind"] == "flatpak" else "Google Chrome"
        return {"job": self.jobs.start(flatpak_id, label), "installId": flatpak_id}

    def api_favorite(self, body: dict) -> dict:
        app = self._app(body)
        value = bool(body.get("value"))

        def apply(d: dict) -> None:
            favs = [f for f in d["favorites"] if f != app["id"]]
            if value:
                favs.append(app["id"])
            d["favorites"] = favs
        return {"favorites": self.store.update(apply)["favorites"]}

    def api_hide(self, body: dict) -> dict:
        app = self._app(body)
        value = bool(body.get("value"))

        def apply(d: dict) -> None:
            hidden = [h for h in d["hidden"] if h != app["id"]]
            if value:
                hidden.append(app["id"])
                d["favorites"] = [f for f in d["favorites"] if f != app["id"]]
            d["hidden"] = hidden
        snap = self.store.update(apply)
        return {"hidden": snap["hidden"], "favorites": snap["favorites"]}

    def api_unhide_all(self, body: dict) -> dict:
        return {"hidden": self.store.update(lambda d: d.__setitem__("hidden", []))["hidden"]}

    def api_custom_add(self, body: dict) -> dict:
        name = str(body.get("name") or "").strip()[:40]
        url = normalize_web_url(str(body.get("url") or ""))
        category = body.get("category") if body.get("category") in CUSTOM_CATEGORIES else "watch"
        if not name:
            raise ApiError(400, "bad_name", "Gib der Kachel einen Namen.")
        if not valid_web_url(url):
            raise ApiError(400, "bad_url", "Das sieht nicht nach einer gültigen Web-Adresse aus.")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "web"
        item = {"id": f"custom-{slug}-{secrets.token_hex(3)}", "name": name, "url": url,
                "category": category, "icon": "globe"}

        def apply(d: dict) -> None:
            d["custom"].append(item)
            d["favorites"].append(item["id"])
        self.store.update(apply)
        self._refresh_launchers()
        return {"id": item["id"]}

    def api_custom_remove(self, body: dict) -> dict:
        app = self._app(body)
        if not app.get("custom"):
            raise ApiError(409, "not_custom", "Nur eigene Kacheln lassen sich löschen.")

        def apply(d: dict) -> None:
            d["custom"] = [c for c in d["custom"] if c["id"] != app["id"]]
            d["favorites"] = [f for f in d["favorites"] if f != app["id"]]
            d["hidden"] = [h for h in d["hidden"] if h != app["id"]]
        self.store.update(apply)
        self._refresh_launchers()
        return {}

    def api_settings(self, body: dict) -> dict:
        changes = {}
        for key in ("closeOnLaunch", "reducedEffects", "autoUpdate"):
            if key in body:
                changes[key] = bool(body[key])
        if "browser" in body:
            wanted = body["browser"]
            ids = {b.id for b in available_browsers(self.flatpaks.installed())}
            if wanted is not None and wanted not in ids:
                raise ApiError(400, "bad_browser", "Dieser Browser ist nicht installiert.")
            changes["browser"] = wanted
        if "autostart" in body:
            set_autostart(bool(body["autostart"]))
        snap = self.store.update(lambda d: d["settings"].update(changes)) if changes else self.store.snapshot()
        return {"settings": {**snap["settings"], "autostart": AUTOSTART_FILE.exists()}}

    def api_volume(self, body: dict) -> dict:
        if "percent" in body:
            try:
                percent = max(0, min(100, int(body["percent"])))
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "bad_request") from exc
            volume_set(percent=percent)
        if body.get("mute") == "toggle":
            volume_set(mute="toggle")
        self._invalidate_status()
        return {"volume": volume_get()}

    def api_brightness(self, body: dict) -> dict:
        try:
            percent = int(body.get("percent"))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "bad_request") from exc
        self.brightness.set(percent)
        self._invalidate_status()
        return {"brightness": {"percent": self.brightness.get()}}

    def api_power(self, body: dict) -> dict:
        action = body.get("action")
        if action == "gamemode":
            to_gaming_mode()
        elif action in POWER_COMMANDS:
            spawn(POWER_COMMANDS[action], "system")
        else:
            raise ApiError(400, "bad_action")
        return {}

    def api_steam_add(self, body: dict) -> dict:
        """Open Steam's "Add a Non-Steam Game" list so the app shows up in Gaming Mode too."""
        app = self._app(body)
        if not (DEMO or (shutil.which("steam") and shutil.which("xdg-open"))):
            raise ApiError(409, "no_steam", "Steam läuft gerade nicht.")
        spawn(["xdg-open", "steam://AddNonSteamGame"], "system")
        name = app.get("steamName") or app["name"]
        notify("Zu Steam hinzufügen", f"Hak „{name}“ in der Liste an und klick auf „Ausgewählte Programme hinzufügen“.")
        if self.store.snapshot()["settings"].get("closeOnLaunch") and not DEMO:
            threading.Timer(2.5, close_hub_ui).start()
        return {"steamName": name}

    def api_updates_check(self, body: dict) -> dict:
        self.updater.check_async()
        return {}

    def api_updates_run(self, body: dict) -> dict:
        started = self.updater.run(auto=False, include_self=body.get("self", True) is not False)
        return {"started": started, "updates": self.updater.summary()}

    def api_whatsnew_seen(self, body: dict) -> dict:
        self.store.update(lambda d: d.__setitem__("seenVersion", VERSION))
        return {}

    def api_hub_close(self, body: dict) -> dict:
        if not DEMO:
            threading.Timer(0.25, close_hub_ui).start()
        return {}

    def api_quit(self, body: dict) -> dict:
        if self.shutdown_cb:
            threading.Timer(0.2, self.shutdown_cb).start()
        return {}


MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon",
}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = f"NovaDeck/{VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401 - quiet by default
        log(fmt % args)

    @property
    def ctx(self) -> Context:
        return self.server.ctx  # type: ignore[attr-defined]

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), MIME[".json"], {"Cache-Control": "no-store"})

    def _host_ok(self) -> bool:
        # Blocks DNS-rebinding: only our own loopback origin may talk to us.
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "").lower() in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _authed(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Nova-Token", ""), self.ctx.token)

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"Forbidden", MIME[".txt"])
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/ping":
            return self._json(200, {"app": "novadeck", "version": VERSION, "pid": os.getpid(), "dir": str(APP_DIR)})
        if path.startswith("/api/"):
            if not self._authed():
                return self._json(401, {"ok": False, "error": "unauthorized"})
            if path == "/api/state":
                return self._json(200, self.ctx.full_state())
            if path == "/api/status":
                return self._json(200, self.ctx.status())
            return self._json(404, {"ok": False, "error": "not_found"})
        self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"Forbidden", MIME[".txt"])
        if not self._authed():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self._json(415, {"ok": False, "error": "json_only"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 65536:
            return self._json(413, {"ok": False, "error": "too_large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"ok": False, "error": "bad_json"})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "error": "bad_json"})
        handler = self.ctx.routes.get(urllib.parse.urlsplit(self.path).path)
        if not handler:
            return self._json(404, {"ok": False, "error": "not_found"})
        try:
            result = handler(body)
        except ApiError as exc:
            return self._json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
        except Exception as exc:  # pragma: no cover - last line of defence
            log(f"error in {self.path}: {exc!r}")
            return self._json(500, {"ok": False, "error": "internal", "message": "Da ist etwas schiefgelaufen."})
        self._json(200, {"ok": True, **result})

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        root = WEB_DIR.resolve()
        target = (root / urllib.parse.unquote(path).lstrip("/")).resolve()
        if os.path.commonpath([str(target), str(root)]) != str(root) or not target.is_file():
            return self._send(404, b"Not found", MIME[".txt"])
        data = target.read_bytes()
        ext = target.suffix.lower()
        headers = {"Cache-Control": "no-cache" if ext in (".html", ".js", ".css", ".json") else "max-age=86400"}
        if target.name == "index.html":
            data = data.replace(b"__NOVA_TOKEN__", self.ctx.token.encode())
            headers.update({"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "Cache-Control": "no-store"})
        self._send(200, data, MIME.get(ext, "application/octet-stream"), headers)


class NovaServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, ctx: Context):
        super().__init__(address, Handler)
        self.ctx = ctx


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy


def ping(timeout: float = 0.6) -> dict | None:
    try:
        with _OPENER.open(f"http://{HOST}:{PORT}/api/ping", timeout=timeout) as res:
            return json.load(res)
    except Exception:  # noqa: BLE001 - any failure means "not running"
        return None


def _post_local(path: str, body: dict, timeout: float = 3.0) -> dict | None:
    try:
        token = (RUNTIME_DIR / "token").read_text().strip()
    except OSError:
        return None
    req = urllib.request.Request(f"http://{HOST}:{PORT}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Nova-Token": token})
    try:
        with _OPENER.open(req, timeout=timeout) as res:
            return json.load(res)
    except Exception:  # noqa: BLE001
        return None


def stop_server() -> None:
    if ping() is None:
        return
    if _post_local("/api/quit", {}) is None:
        try:
            os.kill(int((RUNTIME_DIR / "pid").read_text().strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    for _ in range(30):
        if ping(0.3) is None:
            return
        time.sleep(0.1)


def ensure_server() -> None:
    info = ping()
    if info and info.get("version") == VERSION and info.get("dir") == str(APP_DIR):
        return
    if info:
        stop_server()  # an older version is still running, replace it
    spawn([sys.executable, str(Path(__file__).resolve()), "serve"], "server")
    for _ in range(80):
        time.sleep(0.1)
        if ping(0.3):
            return
    raise SystemExit(f"NovaDeck-Dienst startet nicht. Details: {STATE_DIR / 'server.log'}")


def cmd_serve(args: argparse.Namespace) -> int:
    token = secrets.token_urlsafe(24)
    ctx = Context(token)
    try:
        httpd = NovaServer((HOST, PORT), ctx)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {PORT} ist belegt – läuft NovaDeck schon?", file=sys.stderr)
            return 1
        raise
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RUNTIME_DIR, 0o700)
    token_file = RUNTIME_DIR / "token"
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)
    (RUNTIME_DIR / "pid").write_text(str(os.getpid()))

    def stop(*_args) -> None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    ctx.shutdown_cb = stop
    threading.Thread(target=ctx.updater.background, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"NovaDeck {VERSION} läuft auf http://{HOST}:{PORT}/" + (" (Demo)" if DEMO else ""), flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        for name in ("token", "pid"):
            try:
                (RUNTIME_DIR / name).unlink()
            except OSError:
                pass
    return 0


def cmd_hub(args: argparse.Namespace) -> int:
    if args.autostart:
        time.sleep(2.5)  # let Plasma finish starting up first
        settings = Store(load_catalog()).snapshot()["settings"]
        if settings.get("autoUpdate") and not DEMO:
            remote = fetch_remote_version(timeout=5)
            if remote and parse_version(remote["version"]) > parse_version(VERSION):
                try:
                    apply_self_update(reopen=True)  # the new version opens the hub when done
                    print(f"NovaDeck wird auf {remote['version']} aktualisiert …")
                    return 0
                except Exception as exc:  # noqa: BLE001 - never block the hub because of an update
                    log(f"self-update failed: {exc!r}")
    ensure_server()
    catalog = load_catalog()
    name = open_hub_ui(Store(catalog), FlatpakIndex())
    print(f"NovaDeck geöffnet ({name}).")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    store = Store(catalog)
    state = store.snapshot()
    app = find_app(catalog, state, args.app_id)
    if not app:
        print(f"Unbekannte App: {args.app_id}", file=sys.stderr)
        return 2
    gaming = in_gaming_mode()
    try:
        argv = launch_argv(app, state["settings"], FlatpakIndex().installed(), gaming)
    except LaunchError as exc:
        print(exc.message, file=sys.stderr)
        notify("NovaDeck", exc.message)
        return 1
    if gaming or args.foreground:
        # Gaming Mode tracks the process it started, so become the app.
        os.execvp(argv[0], argv)
    spawn(argv)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    close_hub_ui()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    close_hub_ui()
    stop_server()
    return 0


def cmd_desktop_files(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    paths = write_desktop_files(catalog, Store(catalog).snapshot())
    if args.autostart is not None:
        set_autostart(args.autostart == "on")
    print(f"{len(paths)} Starter geschrieben nach {APPLICATIONS_DIR}")
    return 0


def cmd_remove_desktop_files(args: argparse.Namespace) -> int:
    remove_desktop_files()
    set_autostart(False)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    flatpaks = FlatpakIndex().installed()
    state = Store(load_catalog()).snapshot()
    browser = pick_browser(state["settings"], flatpaks)
    checks = [
        ("NovaDeck", VERSION),
        ("Python", sys.version.split()[0]),
        ("SteamOS", "ja" if is_steamos() else "nein"),
        ("Dienst", "läuft" if ping() else "aus"),
        ("Browser", browser.name if browser else "FEHLT (Google Chrome installieren)"),
        ("Flatpak", "ok" if shutil.which("flatpak") else "fehlt"),
        ("Steam", "ok" if shutil.which("steam") else "fehlt"),
        ("Lautstärke", "ok" if volume_get() else "nicht verfügbar"),
        ("Helligkeit", Brightness().detect() or "nicht verfügbar"),
        ("Netzwerk", (network_status() or {}).get("name") or "unbekannt"),
        ("Gaming-Modus", "ok" if shutil.which("steamos-session-select") else "nur Abmelden"),
        ("Autostart", "an" if AUTOSTART_FILE.exists() else "aus"),
        ("Logs", str(STATE_DIR)),
    ]
    width = max(len(k) for k, _ in checks)
    for key, value in checks:
        print(f"  {key.ljust(width)}  {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novadeck", description="NovaDeck – Hub für den Steam-Deck-Desktop-Modus")
    parser.add_argument("--version", action="version", version=f"NovaDeck {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="Hub-Dienst im Vordergrund starten").set_defaults(fn=cmd_serve)
    p = sub.add_parser("hub", help="Hub öffnen (startet den Dienst bei Bedarf)")
    p.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_hub)
    p = sub.add_parser("launch", help="Eine App aus dem Katalog starten")
    p.add_argument("app_id")
    p.add_argument("--foreground", action="store_true", help="Prozess ersetzen statt im Hintergrund starten")
    p.set_defaults(fn=cmd_launch)
    sub.add_parser("close", help="Hub-Fenster schließen").set_defaults(fn=cmd_close)
    sub.add_parser("stop", help="Hub-Fenster und Dienst beenden").set_defaults(fn=cmd_stop)
    p = sub.add_parser("desktop-files", help="Starter fürs App-Menü schreiben")
    p.add_argument("--autostart", choices=("on", "off"))
    p.set_defaults(fn=cmd_desktop_files)
    sub.add_parser("remove-desktop-files", help=argparse.SUPPRESS).set_defaults(fn=cmd_remove_desktop_files)
    sub.add_parser("doctor", help="Systemcheck anzeigen").set_defaults(fn=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
