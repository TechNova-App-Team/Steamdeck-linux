"""Tests for novadeck/library.py against a fake Steam + Prism Launcher setup."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("library", ROOT / "novadeck" / "library.py")
lib = importlib.util.module_from_spec(spec)
sys.modules["library_under_test"] = lib
spec.loader.exec_module(lib)


def write(path: Path, text: str = "", data: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is not None:
        path.write_bytes(data)
    else:
        path.write_text(text)
    return path


class VdfTest(unittest.TestCase):
    def test_parse_nested_and_escapes(self):
        data = lib.parse_vdf('''
            "AppState"
            {
                "appid"     "620"
                "name"      "Portal \\"2\\""
                "UserConfig" { "language" "german" }
            }''')
        self.assertEqual(lib.ci(data, "appstate", "APPID"), "620")
        self.assertEqual(data["AppState"]["name"], 'Portal "2"')
        self.assertEqual(lib.ci(data, "AppState", "UserConfig", "language"), "german")
        self.assertIsNone(lib.ci(data, "AppState", "missing"))


class SteamTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.steam = self.home / ".local/share/Steam"
        self.sd = self.home / "sdcard"
        write(self.steam / "steamapps/libraryfolders.vdf", f'''
            "libraryfolders" {{
              "0" {{ "path" "{self.steam}" }}
              "1" {{ "path" "{self.sd}" }}
            }}''')
        write(self.steam / "steamapps/appmanifest_620.acf", '"AppState" { "appid" "620" "name" "Portal 2" "StateFlags" "4" }')
        write(self.steam / "steamapps/appmanifest_1493710.acf",
              '"AppState" { "appid" "1493710" "name" "Proton Experimental" "StateFlags" "4" }')
        write(self.sd / "steamapps/appmanifest_1145360.acf",
              '"AppState" { "appid" "1145360" "name" "Hades" "StateFlags" "4" "LastPlayed" "1700000000" }')
        write(self.steam / "config/loginusers.vdf",
              '"users" { "76561197960265829" { "AccountName" "x" "MostRecent" "1" } }')  # account id 101
        write(self.steam / "userdata/101/config/localconfig.vdf", '''
            "UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" {
              "620" { "LastPlayed" "1800000000" "Playtime" "754" }
              "1145360" { "Playtime" "45" }
            } } } } }''')
        # art: old flat layout for Portal 2, new nested layout for Hades, custom grid for Portal 2 hero
        write(self.steam / "appcache/librarycache/620_library_600x900.jpg", data=b"portal-cover")
        write(self.steam / "appcache/librarycache/1145360/a1b2c3/library_600x900.jpg", data=b"hades-cover")
        write(self.steam / "userdata/101/config/grid/620_hero.png", data=b"custom-hero")
        self.patch = mock.patch.object(lib, "STEAM_ROOTS", [self.steam])
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_installed_games_from_all_libraries_without_tools(self):
        games = lib.installed_games(self.steam)
        self.assertEqual(sorted(g["name"] for g in games.values()), ["Hades", "Portal 2"])

    def test_active_user_and_history(self):
        user = lib.active_user_dir(self.steam)
        self.assertEqual(user.name, "101")
        history = lib.play_history(user)
        self.assertEqual(history[620], {"lastPlayed": 1800000000, "playtime": 754})

    def test_art_old_and_new_layout_and_custom_grid(self):
        user = lib.active_user_dir(self.steam)
        portal = lib.steam_art(self.steam, user, 620)
        hades = lib.steam_art(self.steam, user, 1145360)
        self.assertEqual(portal["portrait"].read_bytes(), b"portal-cover")
        self.assertEqual(portal["hero"].read_bytes(), b"custom-hero")
        self.assertEqual(hades["portrait"].read_bytes(), b"hades-cover")

    def test_library_items_sorted_data_and_art_endpoint(self):
        library = lib.Library(self.home / "cache")
        with mock.patch.object(lib, "PRISM_ROOTS", []):
            items = {i["name"]: i for i in library.scan()}
        self.assertEqual(items["Portal 2"]["lastPlayed"], 1800000000)
        self.assertIn("13 Std. gespielt", items["Portal 2"]["subtitle"])
        self.assertEqual(items["Hades"]["lastPlayed"], 1700000000)
        self.assertEqual(library.art_file("steam-620", "portrait"), (b"portal-cover", "image/jpeg"))
        self.assertIsNone(library.art_file("steam-620", "../../etc/passwd"))
        self.assertIsNone(library.art_file("steam-999", "portrait"))
        self.assertEqual(lib.launch_argv(library.launch_info("steam-620")), ["xdg-open", "steam://rungameid/620"])

    def test_missing_cover_uses_cdn_once(self):
        library = lib.Library(self.home / "cache")
        with mock.patch.object(lib, "PRISM_ROOTS", []):
            library.scan()
        with mock.patch.object(lib.urllib.request, "urlopen", side_effect=OSError("offline")) as fake:
            self.assertIsNone(library.art_file("steam-1145360", "hero"))
            self.assertIsNone(library.art_file("steam-1145360", "hero"))
        self.assertEqual(fake.call_count, 1, "a miss is remembered instead of asking again")


class PrismTest(unittest.TestCase):
    def test_instances_with_version_world_icon_and_launch(self):
        root = Path(tempfile.mkdtemp())
        inst = root / "instances" / "My Survival"
        write(inst / "instance.cfg", "[General]\nname=Survival mit Freunden\nlastLaunchTime=1800000000000\ntotalTimePlayed=7200\n")
        write(inst / "mmc-pack.json", json.dumps({"components": [{"uid": "net.minecraft", "version": "1.21.4"}]}))
        write(inst / ".minecraft/saves/Welt 1/icon.png", data=b"png")
        write(root / "instances" / "_LAUNCHER_TEMP" / "instance.cfg", "name=tmp\n")
        found = lib.minecraft_instances(root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "Survival mit Freunden")
        self.assertEqual(found[0]["version"], "1.21.4")
        self.assertEqual(found[0]["lastPlayed"], 1800000000)
        self.assertEqual(found[0]["playtime"], 120)
        self.assertTrue(found[0]["world"].name == "icon.png")
        with mock.patch.object(lib.shutil, "which", return_value=None):
            argv = lib.launch_argv({"kind": "minecraft", "instance": "My Survival"})
        self.assertEqual(argv, ["flatpak", "run", "org.prismlauncher.PrismLauncher", "--launch", "My Survival"])


class DemoTest(unittest.TestCase):
    def test_demo_library_has_covers(self):
        library = lib.Library(Path(tempfile.mkdtemp()), demo=True)
        items = library.scan()
        self.assertGreaterEqual(len(items), 5)
        data, ctype = library.art_file(items[0]["id"], "portrait")
        self.assertEqual(ctype, "image/svg+xml")
        self.assertTrue(data.startswith(b"<svg"))


if __name__ == "__main__":
    unittest.main()
