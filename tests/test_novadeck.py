"""Unit and API tests for novadeck.py (standard library only).

Run:  python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="novadeck-test-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                 ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"), ("XDG_RUNTIME_DIR", "run")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)
os.environ["NOVADECK_DEMO"] = "1"

spec = importlib.util.spec_from_file_location("novadeck", ROOT / "novadeck" / "novadeck.py")
nd = importlib.util.module_from_spec(spec)
sys.modules["novadeck"] = nd
spec.loader.exec_module(nd)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HelpersTest(unittest.TestCase):
    def test_valid_web_url(self):
        self.assertTrue(nd.valid_web_url("https://www.netflix.com/browse"))
        self.assertTrue(nd.valid_web_url("http://example.org"))
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://", "ftp://x.y", "https://a b.de",
                    "https://javascript:alert(1)", "https://x.de:99999", 42, None):
            self.assertFalse(nd.valid_web_url(bad), bad)

    def test_normalize_web_url(self):
        self.assertEqual(nd.normalize_web_url(" ardmediathek.de "), "https://ardmediathek.de")
        self.assertEqual(nd.normalize_web_url("localhost:8080"), "https://localhost:8080")
        self.assertEqual(nd.normalize_web_url("http://x.de"), "http://x.de")
        self.assertFalse(nd.valid_web_url(nd.normalize_web_url("javascript:alert(1)")))
        self.assertFalse(nd.valid_web_url(nd.normalize_web_url("data:text/html,hi")))

    def test_exec_quoting(self):
        self.assertEqual(nd._exec_arg("/home/deck/x.py"), "/home/deck/x.py")
        self.assertEqual(nd._exec_arg("100%"), '"100%%"')
        self.assertEqual(nd._exec_arg('a "b" $c'), '"a \\\\"b\\\\" \\\\$c"')

    def test_nmcli_split(self):
        self.assertEqual(nd._split_nmcli(r"yes:My\:Net:78"), ["yes", "My:Net", "78"])

    def test_catalog_is_consistent(self):
        catalog = nd.load_catalog()
        icons = json.loads((nd.WEB_DIR / "icons.json").read_text())
        cats = {c["id"] for c in catalog["categories"]}
        ids = [a["id"] for a in catalog["apps"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate app ids")
        for app in catalog["apps"]:
            self.assertIn(app["category"], cats, app["id"])
            self.assertIn(app["icon"], icons, app["id"])
            self.assertTrue((ROOT / "assets" / "icons" / f"novadeck-{app['icon']}.svg").is_file(), app["id"])
            if app["kind"] == "web":
                self.assertTrue(nd.valid_web_url(app["url"]), app["id"])
        for fav in catalog["defaultFavorites"]:
            self.assertIn(fav, ids)


class UpdateTest(unittest.TestCase):
    def test_version_file_matches(self):
        info = json.loads((ROOT / "novadeck" / "version.json").read_text())
        self.assertEqual(nd.VERSION, info["version"])
        self.assertTrue(info["notes"])
        install = (ROOT / "install.sh").read_text()
        self.assertIn("novadeck/version.json", install)

    def test_parse_version(self):
        self.assertGreater(nd.parse_version("1.10.0"), nd.parse_version("1.9.3"))
        self.assertEqual(nd.parse_version("v2.0"), (2, 0))
        self.assertEqual(nd.parse_version(""), (0,))

    def _tar(self, entries):
        import io
        import tarfile
        path = Path(tempfile.mkdtemp()) / "pkg.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, data in entries:
                info = tarfile.TarInfo(name)
                if data is None:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/etc/passwd"
                    tar.addfile(info)
                else:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        return path

    def test_safe_extract_accepts_github_layout(self):
        archive = self._tar([("repo-abc/install.sh", b"#!/bin/sh\n"), ("repo-abc/novadeck/novadeck.py", b"x"),
                             ("repo-abc/evil-link", None)])
        dest = Path(tempfile.mkdtemp())
        root = nd.safe_extract(archive, dest)
        self.assertEqual(root.name, "repo-abc")
        self.assertFalse((root / "evil-link").exists(), "symlinks must be skipped")

    def test_safe_extract_rejects_traversal(self):
        archive = self._tar([("repo/../../outside.txt", b"x")])
        with self.assertRaises(ValueError):
            nd.safe_extract(archive, Path(tempfile.mkdtemp()))
        archive = self._tar([("/abs.txt", b"x")])
        with self.assertRaises(ValueError):
            nd.safe_extract(archive, Path(tempfile.mkdtemp()))

    def test_safe_extract_needs_installer(self):
        archive = self._tar([("repo/readme.txt", b"x")])
        with self.assertRaises(ValueError):
            nd.safe_extract(archive, Path(tempfile.mkdtemp()))

    def test_remote_version_offline_is_none(self):
        with mock.patch.object(nd.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertIsNone(nd.fetch_remote_version())

    def test_pending_updates_parsing(self):
        def fake_run(argv, **kwargs):
            out = b"com.google.Chrome\norg.freedesktop.Platform.GL.default\n" if "--user" in argv else b"com.spotify.Client\n"
            return mock.Mock(returncode=0, stdout=out)
        listing = "com.google.Chrome\tGoogle Chrome\ncom.spotify.Client\tSpotify\n"
        with mock.patch.object(nd, "run_out", return_value=listing), \
                mock.patch.object(nd.subprocess, "run", side_effect=fake_run):
            apps, others, worked = nd.flatpak_pending_updates()
        self.assertEqual([a["name"] for a in apps], ["Google Chrome", "Spotify"])
        self.assertEqual(others, 1)
        self.assertTrue(worked)

    def test_old_state_gets_whats_new(self):
        nd.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        nd.STATE_FILE.write_text(json.dumps({"last": "netflix"}))
        self.assertEqual(nd.Store(nd.load_catalog()).data["seenVersion"], "1.0.0")
        nd.STATE_FILE.unlink()
        self.assertEqual(nd.Store(nd.load_catalog()).data["seenVersion"], nd.VERSION)


class BrowserTest(unittest.TestCase):
    def setUp(self):
        self.chrome = nd.Browser("com.google.Chrome", "Google Chrome", "flatpak", "chromium")
        self.firefox = nd.Browser("org.mozilla.firefox", "Firefox", "flatpak", "firefox")

    def test_webapp_desktop_mode(self):
        argv = self.chrome.webapp_command("https://www.netflix.com", gaming=False)
        self.assertEqual(argv[:3], ["flatpak", "run", "com.google.Chrome"])
        self.assertIn("--app=https://www.netflix.com", argv)

    def test_webapp_gaming_mode(self):
        argv = self.chrome.webapp_command("https://www.netflix.com", gaming=True)
        self.assertIn("--kiosk", argv)
        self.assertEqual(argv[-1], "https://www.netflix.com")

    def test_hub_profile_is_separate(self):
        argv = self.chrome.hub_command("http://127.0.0.1:1/")
        profile = next(a for a in argv if a.startswith("--user-data-dir="))
        self.assertIn(nd.HUB_PROFILE_DIRNAME, profile)
        self.assertIn(".var/app/com.google.Chrome", profile)
        self.assertIn("--start-fullscreen", argv)

    def test_firefox_fallback(self):
        self.assertIn("--new-window", self.firefox.webapp_command("https://x.de", gaming=False))

    def test_pick_browser_respects_preference(self):
        flatpaks = {"com.google.Chrome", "org.mozilla.firefox"}
        self.assertEqual(nd.pick_browser({"browser": None}, flatpaks).id, "com.google.Chrome")
        self.assertEqual(nd.pick_browser({"browser": "org.mozilla.firefox"}, flatpaks).id, "org.mozilla.firefox")
        self.assertIsNone(nd.pick_browser({}, set()))


class LaunchTest(unittest.TestCase):
    def test_flatpak_not_installed(self):
        app = {"id": "x", "name": "X", "kind": "flatpak", "flatpak": "org.example.X"}
        with self.assertRaises(nd.LaunchError) as ctx:
            nd.launch_argv(app, {}, set(), gaming=False)
        self.assertEqual(ctx.exception.code, "not_installed")

    def test_web_without_browser(self):
        app = {"id": "n", "name": "N", "kind": "web", "url": "https://n.de"}
        with self.assertRaises(nd.LaunchError) as ctx:
            nd.launch_argv(app, {}, set(), gaming=False)
        self.assertEqual(ctx.exception.code, "no_browser")

    def test_describe_offers_chrome_for_web_apps(self):
        app = {"id": "n", "name": "N", "kind": "web", "url": "https://n.de", "category": "watch", "icon": "play"}
        described = nd.describe_app(app, set(), None)
        self.assertFalse(described["available"])
        self.assertEqual(described["installId"], nd.CHROME_FLATPAK)


class StoreTest(unittest.TestCase):
    def test_corrupt_state_falls_back_to_defaults(self):
        nd.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        nd.STATE_FILE.write_text("{ not json")
        store = nd.Store(nd.load_catalog())
        self.assertEqual(store.data["favorites"], nd.load_catalog()["defaultFavorites"])
        nd.STATE_FILE.unlink()

    def test_invalid_custom_tiles_are_dropped(self):
        nd.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        nd.STATE_FILE.write_text(json.dumps({"custom": [
            {"id": "custom-ok", "name": "OK", "url": "https://ok.de", "category": "watch"},
            {"id": "custom-bad", "name": "Bad", "url": "javascript:alert(1)", "category": "watch"},
        ]}))
        store = nd.Store(nd.load_catalog())
        self.assertEqual([c["id"] for c in store.data["custom"]], ["custom-ok"])
        nd.STATE_FILE.unlink()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = nd.Context("test-token")
        cls.httpd = nd.NovaServer(("127.0.0.1", 0), cls.ctx)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, path, body=None, token="test-token", host=None):
        headers = {"X-Nova-Token": token}
        if host:
            headers["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers,
                                         method="POST" if body is not None else "GET")
        try:
            with OPENER.open(request, timeout=5) as res:
                raw = res.read()
                return res.status, json.loads(raw) if path.startswith("/api/") else raw
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw)
            except ValueError:
                return err.code, raw

    def test_ping_needs_no_token(self):
        status, body = self.req("/api/ping", token="")
        self.assertEqual(status, 200)
        self.assertEqual(body["app"], "novadeck")

    def test_api_needs_token(self):
        self.assertEqual(self.req("/api/state", token="wrong")[0], 401)
        self.assertEqual(self.req("/api/launch", {"id": "netflix"}, token="")[0], 401)

    def test_foreign_host_is_rejected(self):
        self.assertEqual(self.req("/api/state", host="evil.example:80")[0], 403)
        self.assertEqual(self.req("/", host=f"attacker.test:{self.port}")[0], 403)

    def test_index_gets_token_and_csp(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/")
        with OPENER.open(request, timeout=5) as res:
            html = res.read().decode()
            self.assertIn('content="test-token"', html)
            self.assertIn("frame-ancestors 'none'", res.headers["Content-Security-Policy"])

    def test_path_traversal_blocked(self):
        status, _ = self.req("/../novadeck.py")
        self.assertEqual(status, 404)
        status, _ = self.req("/%2e%2e/catalog.json")
        self.assertEqual(status, 404)

    def test_state_and_launch(self):
        status, state = self.req("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(any(a["id"] == "netflix" and a["available"] for a in state["apps"]))
        status, body = self.req("/api/launch", {"id": "netflix"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.req("/api/launch", {"id": "nope"})[0], 404)

    def test_custom_tile_roundtrip(self):
        status, body = self.req("/api/custom/add", {"name": "ARD Mediathek", "url": "ardmediathek.de", "category": "watch"})
        self.assertEqual(status, 200, body)
        new_id = body["id"]
        state = self.req("/api/state")[1]
        tile = next(a for a in state["apps"] if a["id"] == new_id)
        self.assertEqual(tile["url"], "https://ardmediathek.de")
        self.assertIn(new_id, state["favorites"])
        launcher = nd.APPLICATIONS_DIR / f"novadeck-{new_id}.desktop"
        self.assertTrue(launcher.is_file())
        self.assertEqual(self.req("/api/custom/remove", {"id": new_id})[0], 200)
        self.assertFalse(launcher.exists())
        self.assertEqual(self.req("/api/custom/add", {"name": "x", "url": "javascript:alert(1)"})[0], 400)

    def test_favorites_and_hide(self):
        self.req("/api/favorite", {"id": "twitch", "value": True})
        self.assertIn("twitch", self.req("/api/state")[1]["favorites"])
        status, body = self.req("/api/hide", {"id": "twitch", "value": True})
        self.assertIn("twitch", body["hidden"])
        self.assertNotIn("twitch", body["favorites"])
        self.req("/api/unhide-all", {})
        self.assertEqual(self.req("/api/state")[1]["hidden"], [])

    def test_settings(self):
        status, body = self.req("/api/settings", {"reducedEffects": True, "autostart": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["settings"]["reducedEffects"])
        self.assertTrue(nd.AUTOSTART_FILE.exists())
        self.req("/api/settings", {"reducedEffects": False, "autostart": False})
        self.assertFalse(nd.AUTOSTART_FILE.exists())
        self.assertEqual(self.req("/api/settings", {"browser": "not.installed"})[0], 400)

    def test_install_job(self):
        with mock.patch.object(nd.time, "sleep", lambda s: None):
            status, body = self.req("/api/install", {"id": "discord"})
            self.assertEqual(status, 200)
            self.assertEqual(body["installId"], "com.discordapp.Discord")
            for _ in range(50):
                job = self.req("/api/status")[1]["jobs"].get("com.discordapp.Discord", {})
                if job.get("status") == "done":
                    break
                threading.Event().wait(0.05)
                self.ctx._invalidate_status()
            self.assertEqual(job.get("status"), "done")

    def test_updates_flow(self):
        with mock.patch.object(nd.time, "sleep", lambda s: None):
            self.assertEqual(self.req("/api/updates/check", {})[0], 200)
            for _ in range(100):
                updates = self.req("/api/status")[1]["updates"]
                if updates["checkedAt"] and not updates["checking"]:
                    break
                threading.Event().wait(0.02)
            self.assertGreater(updates["count"], 0)
            status, body = self.req("/api/updates/run", {"self": True})
            self.assertTrue(body["started"])
            for _ in range(100):
                updates = self.req("/api/status")[1]["updates"]
                if not updates["running"] and updates["last"]:
                    break
                threading.Event().wait(0.02)
            self.assertTrue(updates["last"]["ok"])

    def test_whats_new_seen(self):
        self.req("/api/whatsnew/seen", {})
        self.assertIsNone(self.req("/api/state")[1]["whatsNew"])

    def test_rejects_non_json(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/launch", data=b"id=netflix",
                                         headers={"X-Nova-Token": "test-token",
                                                  "Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            OPENER.open(request, timeout=5)
        self.assertEqual(ctx.exception.code, 415)


if __name__ == "__main__":
    unittest.main()
