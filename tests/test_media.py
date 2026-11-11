"""Tests for novadeck/media.py (MPRIS parsing and player choice)."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("media_under_test", ROOT / "novadeck" / "media.py")
media = importlib.util.module_from_spec(spec)
sys.modules["media_under_test"] = media
spec.loader.exec_module(media)

METADATA = '''method return time=1.2 sender=:1.123 -> destination=:1.456 serial=12 reply_serial=2
   variant       array [
         dict entry(
            string "mpris:trackid"
            variant                object path "/com/spotify/track/abc"
         )
         dict entry(
            string "mpris:length"
            variant                uint64 214000000
         )
         dict entry(
            string "mpris:artUrl"
            variant                string "https://i.scdn.co/image/ab67616d"
         )
         dict entry(
            string "xesam:artist"
            variant                array [
                  string "Neon Waves"
                  string "Guest"
               ]
         )
         dict entry(
            string "xesam:title"
            variant                string "Say "Hi" Tonight"
         )
      ]
'''


class ParseTest(unittest.TestCase):
    def test_metadata(self):
        meta = media.parse_metadata(METADATA)
        self.assertEqual(meta["title"], 'Say "Hi" Tonight')
        self.assertEqual(meta["artist"], "Neon Waves, Guest")
        self.assertEqual(meta["artUrl"], "https://i.scdn.co/image/ab67616d")
        self.assertEqual(meta["length"], 214000000)

    def test_status_and_numbers(self):
        self.assertEqual(media.parse_status('   variant       string "Playing"'), "Playing")
        self.assertEqual(media.parse_status(""), "Stopped")
        self.assertEqual(media.parse_number("   variant       int64 61500000"), 61500000)

    def test_app_label_for_web_apps(self):
        self.assertEqual(media.app_label("org.mpris.MediaPlayer2.spotify", {}), "Spotify")
        self.assertEqual(media.app_label("org.mpris.MediaPlayer2.chromium.instance42",
                                         {"url": "https://music.youtube.com/watch?v=1"}), "YouTube Music")


class PlayerChoiceTest(unittest.TestCase):
    def test_prefers_playing_player_and_hides_art_url(self):
        props = {
            ("org.mpris.MediaPlayer2.vlc", "PlaybackStatus"): 'variant string "Paused"',
            ("org.mpris.MediaPlayer2.vlc", "Metadata"): 'dict entry(\n string "xesam:title"\n variant string "Old Song"\n)',
            ("org.mpris.MediaPlayer2.spotify", "PlaybackStatus"): 'variant string "Playing"',
            ("org.mpris.MediaPlayer2.spotify", "Metadata"): METADATA,
            ("org.mpris.MediaPlayer2.spotify", "Position"): "variant int64 61000000",
        }
        with mock.patch.object(media, "list_players", return_value=["org.mpris.MediaPlayer2.vlc", "org.mpris.MediaPlayer2.spotify"]), \
                mock.patch.object(media, "_prop", side_effect=lambda name, prop: props.get((name, prop), "")):
            player = media.NowPlaying(secret="s").current()
        self.assertEqual(player["app"], "Spotify")
        self.assertEqual(player["status"], "Playing")
        self.assertEqual((player["position"], player["length"]), (61, 214))
        self.assertTrue(player["art"].startswith("/media/art/"))
        self.assertNotIn("scdn", player["art"], "the real art URL never reaches the page")

    def test_nothing_playing(self):
        with mock.patch.object(media, "list_players", return_value=[]):
            self.assertIsNone(media.NowPlaying(secret="s").current())

    def test_art_rejects_non_images(self):
        np = media.NowPlaying(secret="s")
        np._art["k"] = "file:///etc/passwd"
        self.assertIsNone(np.art("k"))
        self.assertIsNone(np.art("unknown"))

    def test_demo_controls(self):
        np = media.NowPlaying(secret="s", demo=True)
        self.assertEqual(np.current()["status"], "Playing")
        np.control("playpause")
        self.assertEqual(np.current()["status"], "Paused")
        self.assertFalse(np.control("rm -rf"))


if __name__ == "__main__":
    unittest.main()
