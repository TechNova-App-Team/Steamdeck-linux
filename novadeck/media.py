"""Now playing for the NovaDeck hub.

Reads MPRIS media players from the session D-Bus with `dbus-send` (Spotify,
Chrome tabs such as YouTube Music, VLC, Firefox …) and sends play/pause/skip.
Standard library only.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

PREFIX = "org.mpris.MediaPlayer2."
OBJECT = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
ACTIONS = {"playpause": "PlayPause", "next": "Next", "previous": "Previous"}
APP_NAMES = {"spotify": "Spotify", "chromium": "Chrome", "chrome": "Chrome", "firefox": "Firefox",
             "vlc": "VLC", "brave": "Brave", "edge": "Edge", "elisa": "Elisa", "strawberry": "Strawberry"}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _dbus(*args: str, timeout: float = 1.5) -> str:
    if not shutil.which("dbus-send"):
        return ""
    try:
        res = subprocess.run(["dbus-send", "--session", "--print-reply", *args], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        return res.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def list_players() -> list[str]:
    out = _dbus("--dest=org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames")
    return [n for n in re.findall(r'string "(org\.mpris\.MediaPlayer2\.[^"]+)"', out)]


def _prop(name: str, prop: str) -> str:
    return _dbus(f"--dest={name}", OBJECT, "org.freedesktop.DBus.Properties.Get", f"string:{PLAYER}", f"string:{prop}")


def parse_status(text: str) -> str:
    m = re.search(r'string "(\w+)"', text)
    return m.group(1) if m else "Stopped"


def parse_number(text: str) -> int:
    m = re.search(r"(?:u?int64|u?int32|double)\s+(-?[\d.]+)", text)
    return int(float(m.group(1))) if m else 0


def parse_metadata(text: str) -> dict:
    """Pulls title, artists, album, art URL and length out of dbus-send's a{sv} dump."""
    meta: dict = {}
    for block in text.split("dict entry(")[1:]:
        key = re.search(r'string "([^"]*)"', block)
        if not key:
            continue
        rest = block[key.end():]
        strings = re.findall(r'string "(.*?)"\s*$', rest, re.M)
        name = key.group(1)
        if name == "xesam:artist":
            meta["artist"] = ", ".join(s for s in strings if s)
        elif name in ("xesam:title", "xesam:album", "mpris:artUrl", "xesam:url") and strings:
            meta[name.split(":")[1]] = strings[0]
        elif name == "mpris:length":
            meta["length"] = parse_number(rest)
    return meta


def app_label(bus_name: str, meta: dict) -> str:
    short = bus_name[len(PREFIX):].split(".")[0].lower()
    label = APP_NAMES.get(short, short.capitalize())
    url = meta.get("url", "")
    if label == "Chrome" and url:  # a web app playing in Chrome: name it after the site
        host = urllib.parse.urlsplit(url).netloc.lower()
        for needle, site in (("music.youtube", "YouTube Music"), ("youtube", "YouTube"), ("spotify", "Spotify"),
                             ("netflix", "Netflix"), ("twitch", "Twitch"), ("soundcloud", "SoundCloud")):
            if needle in host:
                return site
    return label


class NowPlaying:
    def __init__(self, secret: str, demo: bool = False):
        self.secret = secret
        self.demo = demo
        self.lock = threading.Lock()
        self._cache: tuple[float, dict | None] = (-1e9, None)
        self._art: dict[str, str] = {}            # key -> art URL of tracks we have shown
        self._art_bytes: dict[str, tuple[bytes, str]] = {}
        self._demo_started = time.time()
        self._demo_playing = True

    # -- reading ---------------------------------------------------------------
    def current(self) -> dict | None:
        with self.lock:
            at, cached = self._cache
            if time.monotonic() - at < 1.0:
                return dict(cached) if cached else None
        player = self._demo() if self.demo else self._read()
        with self.lock:
            self._cache = (time.monotonic(), player)
        return dict(player) if player else None

    def _read(self) -> dict | None:
        best = None
        for name in list_players():
            if "plasma-browser-integration" in name and best:
                continue
            status = parse_status(_prop(name, "PlaybackStatus"))
            if status == "Stopped":
                continue
            meta = parse_metadata(_prop(name, "Metadata"))
            if not meta.get("title") or meta.get("url", "").startswith("http://127.0.0.1:478"):
                continue  # nothing to show, or the hub itself
            rank = (status == "Playing", "spotify" in name)
            if best is None or rank > best[0]:
                best = (rank, name, status, meta)
        if not best:
            return None
        _, name, status, meta = best
        position = parse_number(_prop(name, "Position"))
        return self._public(name, status, meta, position)

    def _public(self, name: str, status: str, meta: dict, position_us: int) -> dict:
        art_url = meta.get("artUrl", "")
        key = ""
        if art_url:
            key = hashlib.sha256(f"{self.secret}|{art_url}".encode()).hexdigest()[:20]
            with self.lock:
                self._art[key] = art_url
                if len(self._art) > 40:
                    self._art.pop(next(iter(self._art)))
        return {
            "bus": name,
            "app": app_label(name, meta),
            "status": status,
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "album": meta.get("album", ""),
            "art": f"/media/art/{key}" if key else None,
            "length": round(meta.get("length", 0) / 1_000_000),
            "position": round(position_us / 1_000_000),
        }

    # -- control -------------------------------------------------------------
    def control(self, action: str) -> bool:
        if action not in ACTIONS:
            return False
        if self.demo:
            if action == "playpause":
                self._demo_playing = not self._demo_playing
            else:
                self._demo_started = time.time()
            self._cache = (-1e9, None)
            return True
        player = self.current()
        if not player:
            return False
        _dbus("--type=method_call", f"--dest={player['bus']}", OBJECT, f"{PLAYER}.{ACTIONS[action]}")
        with self.lock:
            self._cache = (-1e9, None)
        return True

    # -- cover art -----------------------------------------------------------
    def art(self, key: str) -> tuple[bytes, str] | None:
        with self.lock:
            url = self._art.get(key)
            cached = self._art_bytes.get(key)
        if cached:
            return cached
        if not url:
            return None
        data = None
        parts = urllib.parse.urlsplit(url)
        try:
            if parts.scheme == "https":
                req = urllib.request.Request(url, headers={"User-Agent": "NovaDeck"})
                with urllib.request.urlopen(req, timeout=6) as res:
                    if not res.headers.get("Content-Type", "").startswith("image/"):
                        return None
                    data = res.read(5 * 1024 * 1024 + 1)
            elif parts.scheme == "file":
                path = Path(urllib.parse.unquote(parts.path))
                if path.suffix.lower() in IMAGE_EXT and path.is_file() and path.stat().st_size < 5 * 1024 * 1024:
                    data = path.read_bytes()
        except Exception:  # noqa: BLE001 - no art is fine
            return None
        if not data or len(data) > 5 * 1024 * 1024:
            return None
        ctype = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
        with self.lock:
            self._art_bytes[key] = (data, ctype)
            if len(self._art_bytes) > 12:
                self._art_bytes.pop(next(iter(self._art_bytes)))
        return data, ctype

    # -- demo ----------------------------------------------------------------
    def _demo(self) -> dict | None:
        length = 214
        position = int(time.time() - self._demo_started) % length
        key = "demo"
        with self.lock:
            self._art_bytes[key] = (DEMO_ART.encode(), "image/svg+xml")
        return {"bus": "org.mpris.MediaPlayer2.spotify", "app": "Spotify",
                "status": "Playing" if self._demo_playing else "Paused", "title": "Midnight Drive",
                "artist": "Neon Waves", "album": "Afterglow", "art": f"/media/art/{key}",
                "length": length, "position": position}


DEMO_ART = """<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5b5f6e"/><stop offset="1" stop-color="#0d0e12"/></linearGradient></defs>
<rect width="300" height="300" fill="url(#g)"/><circle cx="150" cy="150" r="78" fill="none" stroke="#fff" stroke-opacity=".7" stroke-width="3"/>
<circle cx="150" cy="150" r="12" fill="#fff"/></svg>"""
