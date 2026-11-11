"""Tests for novadeck/phone.py (phone keyboard)."""

import importlib.util
import json
import struct
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("phone_under_test", ROOT / "novadeck" / "phone.py")
phone = importlib.util.module_from_spec(spec)
sys.modules["phone_under_test"] = phone
spec.loader.exec_module(phone)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _ioc(direction, nr, size):  # linux/ioctl.h
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


class UinputTest(unittest.TestCase):
    def test_ioctl_numbers_and_struct_sizes(self):
        self.assertEqual(phone.UI_SET_EVBIT, _ioc(1, 100, 4))
        self.assertEqual(phone.UI_SET_KEYBIT, _ioc(1, 101, 4))
        self.assertEqual(phone.UI_DEV_SETUP, _ioc(1, 3, 92))
        self.assertEqual(phone.UI_DEV_CREATE, _ioc(0, 1, 0))
        self.assertEqual(struct.calcsize(phone.SETUP_FORMAT), 92)
        self.assertEqual(struct.calcsize(phone.EVENT_FORMAT), 24)

    def test_no_uinput_falls_back_gracefully(self):
        kb = phone.VirtualKeyboard(path=str(Path(tempfile.mkdtemp()) / "missing"))
        self.assertFalse(kb.press(phone.KEYS["enter"]))
        self.assertTrue(kb.error)

    def test_layouts(self):
        self.assertEqual(phone.key_combo("z", "de"), (21,))
        self.assertEqual(phone.key_combo("z", "us"), (44,))
        self.assertEqual(phone.key_combo("@", "de"), (phone.KEY_RIGHTALT, 16))
        self.assertEqual(phone.key_combo("A", "us"), (phone.KEY_LEFTSHIFT, 30))
        self.assertEqual(phone.key_combo("/", "de"), (phone.KEY_LEFTSHIFT, 8))
        self.assertIsNone(phone.key_combo("🎮", "de"))

    def test_type_text_uses_clipboard_then_ctrl_v(self):
        kb = mock.Mock()
        kb.press.return_value = True
        pk = phone.PhoneKeyboard(keyboard=kb)
        with mock.patch.object(phone, "set_clipboard", return_value=True) as clip, \
                mock.patch.object(phone.time, "sleep"):
            result = pk.type_text("Grüße 🎮", enter=True)
        clip.assert_called_once_with("Grüße 🎮")
        self.assertEqual([c.args[0] for c in kb.press.call_args_list], [phone.KEYS["paste"], phone.KEYS["enter"]])
        self.assertTrue(result["pasted"])
        self.assertEqual(pk.last_event["preview"], "7 Zeichen", "the text itself is never echoed")

    def test_type_text_without_clipboard_types_keys(self):
        kb = mock.Mock()
        kb.open.return_value = True
        kb.press.return_value = True
        pk = phone.PhoneKeyboard(keyboard=kb)
        with mock.patch.object(phone, "set_clipboard", return_value=False), \
                mock.patch.object(phone, "active_layout", return_value="de"):
            result = pk.type_text("Hi!🎮")
        self.assertEqual([c.args[0] for c in kb.press.call_args_list],
                         [(phone.KEY_LEFTSHIFT, 35), (23,), (phone.KEY_LEFTSHIFT, 2)])
        self.assertIn("🎮", result["message"])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.pk = phone.PhoneKeyboard(demo=True)
        status = self.pk.start()
        self.assertTrue(status["active"], status)
        self.base = status["url"].replace(self.pk.ip, "127.0.0.1")

    def tearDown(self):
        self.pk.stop()

    def req(self, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                         method="POST" if data is not None else "GET")
        try:
            with OPENER.open(request, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def test_page_and_actions_need_the_token(self):
        status, page = self.req(self.base)
        self.assertEqual(status, 200)
        self.assertIn(b"Tastatur", page)
        status, body = self.req(self.base + "/type", {"text": "hallo", "enter": True})
        self.assertEqual(json.loads(body)["ok"], True)
        self.assertTrue(self.pk.status()["connected"])
        wrong = self.base.rsplit("/", 1)[0] + "/not-the-token"
        self.assertEqual(self.req(wrong)[0], 404)
        self.assertEqual(self.req(wrong + "/type", {"text": "x"})[0], 404)
        self.assertEqual(self.req(self.base.split("/k/")[0] + "/")[0], 404)

    def test_unknown_key_and_flood_guard(self):
        status, body = self.req(self.base + "/key", {"key": "rm"})
        self.assertFalse(json.loads(body)["ok"])
        codes = [self.req(self.base + "/ping", {})[0] for _ in range(40)]
        self.assertIn(404, codes, "more than 25 requests per second are refused")

    def test_stop_invalidates_url(self):
        self.pk.stop()
        time.sleep(0.2)
        self.assertFalse(self.pk.status()["active"])
        with self.assertRaises(Exception):
            OPENER.open(self.base, timeout=2)


if __name__ == "__main__":
    unittest.main()
