"""Your phone as keyboard for the Steam Deck.

The hub shows a QR code. Scanning it opens a small page served by the Deck in
your home network (secret token in the address, nothing leaves your network).
Text you send goes to the clipboard and is pasted with a virtual Ctrl+V, so
umlauts, emoji and passwords work with every keyboard layout. Special keys
(Enter, Backspace, arrows …) are sent as real key presses.

The virtual keyboard uses /dev/uinput, which Steam's udev rules already open
up for the logged-in user on SteamOS. Standard library only.
"""

from __future__ import annotations

import fcntl
import http.server
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

PORT_RANGE = range(47821, 47831)
IDLE_TIMEOUT = 45 * 60          # switch off after 45 minutes without a phone
MAX_TEXT = 4000

# linux/uinput.h & input-event-codes.h
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_SETUP = 0x405C5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EV_SYN, EV_KEY, SYN_REPORT, BUS_VIRTUAL = 0, 1, 0, 0x06
KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_A, KEY_V = 29, 42, 30, 47
KEYS = {
    "enter": (28,), "backspace": (14,), "tab": (15,), "esc": (1,), "space": (57,), "delete": (111,),
    "up": (103,), "down": (108,), "left": (105,), "right": (106,), "home": (102,), "end": (107,),
    "selectall": (KEY_LEFTCTRL, KEY_A), "paste": (KEY_LEFTCTRL, KEY_V),
    "pasteterm": (KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V),
}
KEY_RIGHTALT = 100  # AltGr
SETUP_FORMAT = "HHHH80sI"   # struct uinput_setup (92 bytes)
EVENT_FORMAT = "llHHi"      # struct input_event (24 bytes on 64-bit)


class VirtualKeyboard:
    """A tiny keyboard device created through /dev/uinput."""

    def __init__(self, path: str = "/dev/uinput"):
        self.path = path
        self.fd: int | None = None
        self.lock = threading.Lock()
        self.error = ""

    def available(self) -> bool:
        return self.fd is not None or os.access(self.path, os.W_OK)

    def open(self) -> bool:
        if self.fd is not None:
            return True
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            self.error = str(exc)
            return False
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            for code in range(1, 128):  # every key of a normal keyboard (incl. AltGr and the <> key)
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)
            setup = struct.pack(SETUP_FORMAT, BUS_VIRTUAL, 0x4E44, 0x4B42, 1, b"NovaDeck Phone Keyboard", 0)
            fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except OSError as exc:
            self.error = str(exc)
            os.close(fd)
            return False
        self.fd = fd
        time.sleep(0.5)  # let the compositor pick up the new device
        return True

    def _emit(self, ev_type: int, code: int, value: int) -> None:
        os.write(self.fd, struct.pack(EVENT_FORMAT, 0, 0, ev_type, code, value))  # type: ignore[arg-type]

    def press(self, combo: tuple[int, ...]) -> bool:
        with self.lock:
            if not self.open():
                return False
            try:
                for code in combo:
                    self._emit(EV_KEY, code, 1)
                self._emit(EV_SYN, SYN_REPORT, 0)
                time.sleep(0.012)
                for code in reversed(combo):
                    self._emit(EV_KEY, code, 0)
                self._emit(EV_SYN, SYN_REPORT, 0)
                time.sleep(0.012)
                return True
            except OSError as exc:
                self.error = str(exc)
                return False

    def close(self) -> None:
        with self.lock:
            if self.fd is not None:
                try:
                    fcntl.ioctl(self.fd, UI_DEV_DESTROY)
                except OSError:
                    pass
                os.close(self.fd)
                self.fd = None


# ---------------------------------------------------------------- layout typing
# Fallback when the clipboard is not reachable: type plain characters key by key.
# Keycodes are physical keys, so the active layout (QWERTZ vs QWERTY) matters.
_SHIFT, _ALTGR = "s", "a"
_COMMON = {**{str(d): (code,) for d, code in zip("1234567890", range(2, 12))}, " ": (57,), "\n": (28,), "\t": (15,)}
_LETTERS_US = dict(zip("qwertyuiopasdfghjklzxcvbnm",
                       [16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 30, 31, 32, 33, 34, 35, 36, 37, 38, 44, 45, 46, 47, 48, 49, 50]))
_LETTERS_DE = {**_LETTERS_US, "y": 44, "z": 21}
LAYOUTS = {
    "us": {**_COMMON, **{k: (v,) for k, v in _LETTERS_US.items()},
           "-": (12,), "=": (13,), "[": (26,), "]": (27,), ";": (39,), "'": (40,), "`": (41,), "\\": (43,),
           ",": (51,), ".": (52,), "/": (53,),
           "!": (2, _SHIFT), "@": (3, _SHIFT), "#": (4, _SHIFT), "$": (5, _SHIFT), "%": (6, _SHIFT),
           "^": (7, _SHIFT), "&": (8, _SHIFT), "*": (9, _SHIFT), "(": (10, _SHIFT), ")": (11, _SHIFT),
           "_": (12, _SHIFT), "+": (13, _SHIFT), "{": (26, _SHIFT), "}": (27, _SHIFT), ":": (39, _SHIFT),
           '"': (40, _SHIFT), "~": (41, _SHIFT), "|": (43, _SHIFT), "<": (51, _SHIFT), ">": (52, _SHIFT),
           "?": (53, _SHIFT)},
    "de": {**_COMMON, **{k: (v,) for k, v in _LETTERS_DE.items()},
           "ß": (12,), "ü": (26,), "+": (27,), "ö": (39,), "ä": (40,), "#": (43,), ",": (51,), ".": (52,),
           "-": (53,), "<": (86,),
           "!": (2, _SHIFT), '"': (3, _SHIFT), "§": (4, _SHIFT), "$": (5, _SHIFT), "%": (6, _SHIFT),
           "&": (7, _SHIFT), "/": (8, _SHIFT), "(": (9, _SHIFT), ")": (10, _SHIFT), "=": (11, _SHIFT),
           "?": (12, _SHIFT), "*": (27, _SHIFT), "'": (43, _SHIFT), ";": (51, _SHIFT), ":": (52, _SHIFT),
           "_": (53, _SHIFT), ">": (86, _SHIFT), "Ü": (26, _SHIFT), "Ö": (39, _SHIFT), "Ä": (40, _SHIFT),
           "@": (16, _ALTGR), "€": (18, _ALTGR), "{": (8, _ALTGR), "[": (9, _ALTGR), "]": (10, _ALTGR),
           "}": (11, _ALTGR), "\\": (12, _ALTGR), "~": (27, _ALTGR), "|": (86, _ALTGR)},
}


def active_layout() -> str:
    """First layout from Plasma's keyboard settings (defaults to US like SteamOS)."""
    cfg = Path.home() / ".config" / "kxkbrc"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.startswith("LayoutList="):
                first = line.split("=", 1)[1].split(",")[0].strip().lower()
                return first if first in LAYOUTS else "us"
    except OSError:
        pass
    return "us"


def key_combo(char: str, layout: str) -> tuple[int, ...] | None:
    """Keycodes (with modifiers first) that produce `char` on `layout`, or None."""
    table = LAYOUTS.get(layout, LAYOUTS["us"])
    entry = table.get(char)
    if entry is None and char.isalpha() and char.lower() in table and len(table[char.lower()]) == 1:
        entry = (table[char.lower()][0], _SHIFT)
    if entry is None:
        return None
    code, *mods = entry
    prefix = []
    if _SHIFT in mods:
        prefix.append(KEY_LEFTSHIFT)
    if _ALTGR in mods:
        prefix.append(KEY_RIGHTALT)
    return (*prefix, code)


def set_clipboard(text: str) -> bool:
    """Puts text on the desktop clipboard (Klipper, then wl-copy, then xclip/xsel)."""
    if shutil.which("dbus-send"):
        try:
            res = subprocess.run(["dbus-send", "--session", "--print-reply", "--dest=org.kde.klipper", "/klipper",
                                  "org.kde.klipper.klipper.setClipboardContents", f"string:{text}"],
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=2, check=False)
            if res.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(argv[0]):
            try:
                if subprocess.run(argv, input=text.encode(), capture_output=True, timeout=2, check=False).returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def lan_ip() -> str | None:
    """The Deck's address in the home network (no packet is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.254.254.254", 1))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


class PhoneKeyboard:
    def __init__(self, demo: bool = False, keyboard: VirtualKeyboard | None = None):
        self.demo = demo
        self.keyboard = keyboard or VirtualKeyboard()
        self.lock = threading.Lock()
        self.httpd: http.server.ThreadingHTTPServer | None = None
        self.token = ""
        self.port = 0
        self.ip: str | None = None
        self.started = 0.0
        self.last_seen = 0.0
        self.last_event: dict | None = None
        self._hits: list[float] = []

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> dict:
        with self.lock:
            if self.httpd:
                self.started = time.time()
                return self._status()
            self.ip = "192.168.178.42" if self.demo else lan_ip()
            if not self.ip:
                return {"active": False, "error": "Kein WLAN gefunden. Ist das Deck mit deinem Heimnetz verbunden?"}
            self.token = secrets.token_urlsafe(18)
            for port in PORT_RANGE:
                try:
                    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), _make_handler(self))
                except OSError:
                    continue
                httpd.daemon_threads = True
                self.httpd, self.port = httpd, port
                break
            if not self.httpd:
                return {"active": False, "error": "Kein freier Port für die Handy-Verbindung."}
            self.started = time.time()
            self.last_seen = 0.0
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
            threading.Thread(target=self._watchdog, daemon=True).start()
            return self._status()

    def stop(self) -> dict:
        with self.lock:
            httpd, self.httpd = self.httpd, None
            self.token = ""
        if httpd:
            threading.Thread(target=httpd.shutdown, daemon=True).start()
            httpd.server_close()
        self.keyboard.close()
        return self.status()

    def _watchdog(self) -> None:
        while True:
            time.sleep(30)
            with self.lock:
                if not self.httpd:
                    return
                idle_since = max(self.started, self.last_seen)
            if time.time() - idle_since > IDLE_TIMEOUT:
                self.stop()
                return

    def status(self) -> dict:
        with self.lock:
            return self._status()

    def _status(self) -> dict:
        active = self.httpd is not None
        return {
            "active": active,
            "url": f"http://{self.ip}:{self.port}/k/{self.token}" if active else None,
            "connected": active and time.time() - self.last_seen < 25,
            "lastSeen": self.last_seen or None,
            "event": dict(self.last_event) if self.last_event else None,
            "typing": self.demo or self.keyboard.available(),
        }

    # -- actions ----------------------------------------------------------------
    def check(self, token: str) -> bool:
        with self.lock:
            ok = bool(self.token) and secrets.compare_digest(token, self.token)
            if ok:
                now = time.time()
                self._hits = [t for t in self._hits if now - t < 1.0] + [now]
                ok = len(self._hits) <= 25  # simple flood guard
                self.last_seen = now
            return ok

    def _event(self, kind: str, preview: str) -> None:
        self.last_event = {"kind": kind, "preview": preview, "at": time.time()}

    def type_text(self, text: str, enter: bool = False, terminal: bool = False) -> dict:
        text = text.replace("\r\n", "\n")[:MAX_TEXT]
        preview = f"{len(text)} Zeichen"  # never echo the text itself: it may be a password
        if self.demo:
            self._event("text", preview)
            return {"ok": True, "pasted": True}
        if not set_clipboard(text):
            return self._type_keys(text, enter)
        time.sleep(0.12)
        pasted = self.keyboard.press(KEYS["pasteterm" if terminal else "paste"])
        if pasted and enter:
            time.sleep(0.05)
            self.keyboard.press(KEYS["enter"])
        self._event("text", preview)
        if not pasted:
            return {"ok": True, "pasted": False,
                    "message": "Liegt in der Zwischenablage. Auf dem Deck: gedrückt halten → Einfügen."}
        return {"ok": True, "pasted": True}

    def _type_keys(self, text: str, enter: bool) -> dict:
        """No clipboard available: type character by character on the active layout."""
        layout = active_layout()
        combos = [key_combo(ch, layout) for ch in text]
        missing = sorted({ch for ch, combo in zip(text, combos) if combo is None})
        if not self.keyboard.open():
            return {"ok": False, "error": "Weder Zwischenablage noch virtuelle Tastatur sind verfügbar."}
        for combo in combos:
            if combo:
                self.keyboard.press(combo)
        if enter:
            self.keyboard.press(KEYS["enter"])
        self._event("text", f"{len(text)} Zeichen")
        if missing:
            return {"ok": True, "pasted": True, "message": f"Eingetippt, außer: {' '.join(missing)}"}
        return {"ok": True, "pasted": True}

    def key(self, name: str) -> dict:
        combo = KEYS.get(name)
        if not combo:
            return {"ok": False, "error": "Unbekannte Taste."}
        if self.demo:
            self._event("key", name)
            return {"ok": True}
        ok = self.keyboard.press(combo)
        self._event("key", name)
        return {"ok": ok} if ok else {"ok": False, "error": "Die virtuelle Tastatur ist nicht verfügbar."}


def _make_handler(owner: PhoneKeyboard):
    page = (Path(__file__).resolve().parent / "web" / "phone.html").read_text(encoding="utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "NovaDeck"
        sys_version = ""

        def log_message(self, fmt, *args):  # noqa: D401 - quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; connect-src 'self'; img-src data:; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _route(self) -> tuple[str, str] | None:
            parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
            if len(parts) < 2 or parts[0] != "k" or not owner.check(parts[1]):
                return None
            return parts[1], (parts[2] if len(parts) > 2 else "")

        def do_GET(self) -> None:  # noqa: N802
            route = self._route()
            if not route or route[1]:
                return self._send(404, b"Not found", "text/plain")
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            route = self._route()
            if not route:
                return self._send(404, b"{}", "application/json")
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                return self._send(400, b'{"ok":false}', "application/json")
            if not isinstance(body, dict):
                return self._send(400, b'{"ok":false}', "application/json")
            action = route[1]
            if action == "type":
                result = owner.type_text(str(body.get("text", "")), bool(body.get("enter")), bool(body.get("terminal")))
            elif action == "key":
                result = owner.key(str(body.get("key", "")))
            elif action == "ping":
                result = {"ok": True}
            else:
                return self._send(404, b"{}", "application/json")
            self._send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json")

    return Handler
