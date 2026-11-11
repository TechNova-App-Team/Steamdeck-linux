"""Your games for the NovaDeck hub: Steam library and Minecraft (Prism Launcher).

Reads only local files (no Steam API, no account data leaves the Deck). Cover
art comes from Steam's own cache or your custom grid images; only if neither
exists, the public Steam CDN is asked for the cover.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
STEAMID64_BASE = 76561197960265728
STEAM_ROOTS = [
    HOME / ".local" / "share" / "Steam",
    HOME / ".steam" / "steam",
    HOME / ".steam" / "root",
    HOME / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
]
PRISM_ROOTS = [
    HOME / ".var" / "app" / "org.prismlauncher.PrismLauncher" / "data" / "PrismLauncher",
    HOME / ".local" / "share" / "PrismLauncher",
]
PRISM_FLATPAK = "org.prismlauncher.PrismLauncher"
# Proton, runtimes & co. are "apps" in Steam's eyes but not games.
TOOL_NAME = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks Common|Steam Runtime|SteamVR|Steam Audio)", re.I)
TOOL_IDS = {228980, 1070560, 1391110, 1628350, 1493710, 2180100, 961940, 1054830, 1113280,
            1245040, 1420170, 1580130, 1887720, 2348590, 2805730, 250820}
ART_VARIANTS = ("portrait", "hero", "header", "logo", "world")
CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{file}"
CDN_FILES = {"portrait": "library_600x900.jpg", "hero": "library_hero.jpg", "header": "header.jpg", "logo": "logo.png"}


# --------------------------------------------------------------------------- VDF

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Parses Valve's text KeyValues format (libraryfolders.vdf, *.acf, localconfig.vdf)."""
    root: dict = {}
    stack = [root]
    key = None
    for quoted, brace in _TOKEN.findall(text):
        if brace == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            value = quoted.replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    return root


def ci(mapping: dict, *keys: str, default=None):
    """Case-insensitive nested lookup: ci(d, "UserLocalConfigStore", "Software", ...)."""
    cur = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return default
        lowered = key.lower()
        cur = next((v for k, v in cur.items() if k.lower() == lowered), None)
        if cur is None:
            return default
    return cur


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------- Steam

def find_steam_root() -> Path | None:
    for root in STEAM_ROOTS:
        if (root / "steamapps").is_dir():
            return root.resolve()
    return None


def library_dirs(root: Path) -> list[Path]:
    dirs = [root]
    data = parse_vdf(_read(root / "steamapps" / "libraryfolders.vdf"))
    folders = ci(data, "libraryfolders", default={}) or {}
    for entry in folders.values():
        path = ci(entry, "path") if isinstance(entry, dict) else entry
        if isinstance(path, str) and path:
            p = Path(path)
            if p.is_dir() and p.resolve() not in [d.resolve() for d in dirs]:
                dirs.append(p)
    return dirs


def installed_games(root: Path) -> dict[int, dict]:
    games: dict[int, dict] = {}
    for lib in library_dirs(root):
        for manifest in (lib / "steamapps").glob("appmanifest_*.acf"):
            state = ci(parse_vdf(_read(manifest)), "AppState", default={}) or {}
            appid = _int(ci(state, "appid"))
            name = str(ci(state, "name", default="")).strip()
            if not appid or not name or appid in TOOL_IDS or TOOL_NAME.search(name):
                continue
            flags = _int(ci(state, "StateFlags"), 4)
            games[appid] = {
                "appid": appid,
                "name": name,
                "installed": bool(flags & 4),
                "lastPlayed": _int(ci(state, "LastPlayed")),
                "size": _int(ci(state, "SizeOnDisk")),
            }
    return games


def active_user_dir(root: Path) -> Path | None:
    userdata = root / "userdata"
    if not userdata.is_dir():
        return None
    users = ci(parse_vdf(_read(root / "config" / "loginusers.vdf")), "users", default={}) or {}
    for steamid64, info in users.items():
        if isinstance(info, dict) and str(ci(info, "MostRecent", default="0")) == "1":
            account = _int(steamid64) - STEAMID64_BASE
            if account > 0 and (userdata / str(account)).is_dir():
                return userdata / str(account)
    candidates = [d for d in userdata.iterdir() if d.is_dir() and d.name.isdigit() and d.name != "0"]
    candidates = [d for d in candidates if (d / "config" / "localconfig.vdf").is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: (d / "config" / "localconfig.vdf").stat().st_mtime)


def play_history(user_dir: Path | None) -> dict[int, dict]:
    if not user_dir:
        return {}
    data = parse_vdf(_read(user_dir / "config" / "localconfig.vdf"))
    apps = ci(data, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", default={}) or {}
    history = {}
    for appid, info in apps.items():
        if isinstance(info, dict) and appid.isdigit():
            history[int(appid)] = {"lastPlayed": _int(ci(info, "LastPlayed")), "playtime": _int(ci(info, "Playtime"))}
    return history


def _first(paths) -> Path | None:
    for p in paths:
        if p and p.is_file() and p.stat().st_size > 0:
            return p
    return None


def steam_art(root: Path, user_dir: Path | None, appid: int) -> dict[str, Path]:
    """Finds local artwork: your custom grid images first, then Steam's cache (old and new layout)."""
    cache = root / "appcache" / "librarycache"
    nested = cache / str(appid)
    grid = user_dir / "config" / "grid" if user_dir else None
    exts = ("png", "jpg", "jpeg", "webp")

    def grid_file(suffix: str) -> list[Path]:
        return [grid / f"{appid}{suffix}.{ext}" for ext in exts] if grid else []

    def cached(flat: str, pattern: str) -> list[Path]:
        found = [cache / f"{appid}_{flat}"]
        if nested.is_dir():
            found += sorted(nested.glob(pattern)) + sorted(nested.glob(f"*/{pattern}"))
        return found

    art = {
        "portrait": _first(grid_file("p") + cached("library_600x900.jpg", "library_600x900*.jpg")),
        "hero": _first(grid_file("_hero") + cached("library_hero.jpg", "library_hero*.jpg")),
        "header": _first(grid_file("") + cached("header.jpg", "header*.jpg")),
        "logo": _first(grid_file("_logo") + cached("logo.png", "logo*.png")),
    }
    return {k: v for k, v in art.items() if v}


# --------------------------------------------------------------------------- Minecraft

def _read_cfg(path: Path) -> dict[str, str]:
    values = {}
    for line in _read(path).splitlines():
        if "=" in line and not line.lstrip().startswith(("[", "#", ";")):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def prism_root() -> Path | None:
    for root in PRISM_ROOTS:
        if (root / "instances").is_dir():
            return root
    return None


def minecraft_instances(root: Path | None) -> list[dict]:
    if not root:
        return []
    found = []
    for inst in sorted((root / "instances").iterdir()):
        cfg = inst / "instance.cfg"
        if not inst.is_dir() or not cfg.is_file() or inst.name.startswith("_"):
            continue
        values = _read_cfg(cfg)
        version = ""
        try:
            pack = json.loads(_read(inst / "mmc-pack.json") or "{}")
            version = next((c.get("version", "") for c in pack.get("components", []) if c.get("uid") == "net.minecraft"), "")
        except (ValueError, AttributeError):
            pass
        world_icon = None
        for game_dir in (inst / ".minecraft", inst / "minecraft"):
            saves = game_dir / "saves"
            if saves.is_dir():
                worlds = [w for w in saves.iterdir() if (w / "icon.png").is_file()]
                if worlds:
                    world_icon = max(worlds, key=lambda w: w.stat().st_mtime) / "icon.png"
                    break
        found.append({
            "folder": inst.name,
            "name": values.get("name") or inst.name,
            "version": version,
            "lastPlayed": _int(values.get("lastLaunchTime")) // 1000,
            "playtime": _int(values.get("totalTimePlayed")) // 60,
            "world": world_icon,
        })
    return found


# --------------------------------------------------------------------------- library

def _hours(minutes: int) -> str:
    if minutes <= 0:
        return ""
    if minutes < 60:
        return f"{minutes} Min. gespielt"
    hours = minutes / 60
    return f"{hours:.0f} Std. gespielt" if hours >= 10 else f"{hours:.1f} Std. gespielt".replace(".", ",")


class Library:
    """Cached scan of Steam games and Minecraft instances, plus their artwork."""

    def __init__(self, cache_dir: Path, demo: bool = False):
        self.cache_dir = cache_dir / "art"
        self.demo = demo
        self.lock = threading.Lock()
        self.items: list[dict] = []
        self.art: dict[str, dict[str, Path | str]] = {}
        self.launch: dict[str, dict] = {}
        self._at = -1e9

    def scan(self, max_age: float = 60.0) -> list[dict]:
        with self.lock:
            if time.monotonic() - self._at < max_age:
                return [dict(i) for i in self.items]
            items, art, launch = (_demo_library() if self.demo else self._scan())
            self.items, self.art, self.launch, self._at = items, art, launch, time.monotonic()
            return [dict(i) for i in items]

    def _scan(self) -> tuple[list[dict], dict, dict]:
        items, art, launch = [], {}, {}
        root = find_steam_root()
        if root:
            user = active_user_dir(root)
            history = play_history(user)
            for appid, game in installed_games(root).items():
                h = history.get(appid, {})
                last = max(game["lastPlayed"], h.get("lastPlayed", 0))
                playtime = h.get("playtime", 0)
                key = f"steam-{appid}"
                found = steam_art(root, user, appid)
                art[key] = dict(found)
                art[key]["appid"] = str(appid)
                launch[key] = {"kind": "steam", "appid": appid}
                items.append({
                    "id": key, "name": game["name"], "kind": "steam", "category": "play", "icon": "pad",
                    "subtitle": " · ".join(x for x in ("Steam", _hours(playtime)) if x),
                    "info": "Startet über Steam hier im Desktop-Modus. Mit Controller-Layout am besten aus dem Gaming-Modus.",
                    "lastPlayed": last, "installed": game["installed"], "library": True, "appid": appid,
                    "cover": f"/art/{key}/portrait", "hero": f"/art/{key}/hero",
                })
        for inst in minecraft_instances(prism_root()):
            key = "mc-" + hashlib.sha1(inst["folder"].encode()).hexdigest()[:10]
            art[key] = {"world": inst["world"]} if inst["world"] else {}
            launch[key] = {"kind": "minecraft", "instance": inst["folder"]}
            items.append({
                "id": key, "name": inst["name"], "kind": "minecraft", "category": "play", "icon": "cube",
                "subtitle": " · ".join(x for x in (f"Minecraft {inst['version']}".strip(), _hours(inst["playtime"])) if x),
                "info": "Startet diese Minecraft-Instanz direkt, ohne Umweg über den Launcher.",
                "lastPlayed": inst["lastPlayed"], "installed": True, "library": True, "instance": inst["folder"],
                "cover": f"/art/{key}/world" if inst["world"] else None, "hero": None,
            })
        return items, art, launch

    def launch_info(self, item_id: str) -> dict | None:
        self.scan()
        with self.lock:
            return dict(self.launch[item_id]) if item_id in self.launch else None

    def art_file(self, item_id: str, variant: str) -> tuple[bytes, str] | None:
        """Returns (bytes, content type) for an artwork, or None."""
        if variant not in ART_VARIANTS:
            return None
        self.scan()
        with self.lock:
            entry = self.art.get(item_id)
        if entry is None:
            return None
        found = entry.get(variant)
        if isinstance(found, str) and found.startswith("<svg"):
            return found.encode(), "image/svg+xml"
        if isinstance(found, Path):
            try:
                return found.read_bytes(), _ctype(found.name)
            except OSError:
                return None
        appid = entry.get("appid")
        if appid and variant in CDN_FILES:
            return self._cdn(str(appid), variant)
        return None

    def _cdn(self, appid: str, variant: str) -> tuple[bytes, str] | None:
        """Downloads a missing cover once from Steam's public CDN and caches it (also misses)."""
        name = CDN_FILES[variant]
        target = self.cache_dir / f"{appid}-{name}"
        miss = target.with_suffix(target.suffix + ".miss")
        if target.is_file():
            return target.read_bytes(), _ctype(name)
        if miss.exists() and time.time() - miss.stat().st_mtime < 7 * 86400:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(CDN.format(appid=appid, file=name), headers={"User-Agent": "NovaDeck"})
            with urllib.request.urlopen(req, timeout=8) as res:
                data = res.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024 or not data:
                raise ValueError("bad size")
            target.write_bytes(data)
            return data, _ctype(name)
        except Exception:  # noqa: BLE001 - offline or no such artwork
            miss.touch()
            return None


def launch_argv(info: dict) -> list[str]:
    if info["kind"] == "steam":
        return ["xdg-open", f"steam://rungameid/{int(info['appid'])}"]
    if info["kind"] == "minecraft":
        if shutil.which("prismlauncher"):
            return ["prismlauncher", "--launch", info["instance"]]
        return ["flatpak", "run", PRISM_FLATPAK, "--launch", info["instance"]]
    raise ValueError("unknown library item")


def _ctype(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return {"png": "image/png", "webp": "image/webp", "svg": "image/svg+xml"}.get(ext, "image/jpeg")


# --------------------------------------------------------------------------- demo

_DEMO_GAMES = [
    ("Nebula Drift", 4, 2900, (58, 72, 112)), ("Ironvale", 30, 6200, (92, 74, 58)),
    ("Hollow Peaks", 74, 1500, (54, 88, 82)), ("Pixel Kart", 160, 610, (104, 58, 86)),
    ("Starforge", 400, 12400, (70, 70, 78)), ("Echo Run", 900, 300, (60, 86, 110)),
]


def _demo_cover(title: str, rgb: tuple[int, int, int], wide: bool = False) -> str:
    w, h = (1920, 620) if wide else (600, 900)
    r, g, b = rgb
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="rgb({r + 40},{g + 40},{b + 40})"/>
<stop offset="1" stop-color="rgb({r // 4},{g // 4},{b // 4})"/></linearGradient>
<radialGradient id="l" cx=".7" cy=".25" r=".6"><stop offset="0" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>
<rect width="{w}" height="{h}" fill="url(#g)"/><rect width="{w}" height="{h}" fill="url(#l)"/>
<circle cx="{w * .68:.0f}" cy="{h * .34:.0f}" r="{min(w, h) * .22:.0f}" fill="none" stroke="#fff" stroke-opacity=".5" stroke-width="3"/>
<path d="M0 {h * .78:.0f} L{w * .3:.0f} {h * .6:.0f} L{w * .55:.0f} {h * .72:.0f} L{w} {h * .52:.0f} L{w} {h} L0 {h}z" fill="#000" fill-opacity=".35"/>
{"" if wide else f'<text x="{w * .08:.0f}" y="{h * .16:.0f}" fill="#fff" font-family="Inter,sans-serif" font-weight="800" font-size="58" letter-spacing="-1">{title}</text>'}
</svg>"""


def _demo_library() -> tuple[list[dict], dict, dict]:
    now = time.time()
    items, art, launch = [], {}, {}
    for n, (title, hours_ago, minutes, rgb) in enumerate(_DEMO_GAMES):
        key = f"steam-{9000 + n}"
        art[key] = {"portrait": _demo_cover(title, rgb), "hero": _demo_cover(title, rgb, wide=True)}
        launch[key] = {"kind": "steam", "appid": 9000 + n}
        items.append({"id": key, "name": title, "kind": "steam", "category": "play", "icon": "pad",
                      "subtitle": f"Steam · {_hours(minutes)}", "info": "Demo-Spiel.", "installed": True,
                      "lastPlayed": int(now - hours_ago * 3600), "library": True, "appid": 9000 + n,
                      "cover": f"/art/{key}/portrait", "hero": f"/art/{key}/hero"})
    key = "mc-demo"
    art[key] = {"world": _demo_cover("Survival", (60, 96, 60))}
    launch[key] = {"kind": "minecraft", "instance": "Survival"}
    items.append({"id": key, "name": "Survival", "kind": "minecraft", "category": "play", "icon": "cube",
                  "subtitle": "Minecraft 1.21.4 · 42 Std. gespielt", "info": "Demo-Instanz.", "installed": True,
                  "lastPlayed": int(now - 2 * 3600), "library": True, "instance": "Survival",
                  "cover": f"/art/{key}/world", "hero": None})
    return items, art, launch
