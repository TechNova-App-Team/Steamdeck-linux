#!/usr/bin/env python3
"""Fake KDE / Flatpak / D-Bus tools for testing install.sh and uninstall.sh.

Every tool call is appended to $STUB_LOG. Config files are plain INI files in
$XDG_CONFIG_HOME, nested KDE groups are written as [A][B].
"""

import configparser
import os
import sys
from pathlib import Path

LOG = Path(os.environ["STUB_LOG"])
CONFIG = Path(os.environ["XDG_CONFIG_HOME"])
FLATPAKS = Path(os.environ["STUB_FLATPAKS"])


def log(*parts: str) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(" ".join(parts) + "\n")


def parse_kconfig(args: list[str]):
    file = key = None
    groups, rest, delete = [], [], False
    it = iter(args)
    for arg in it:
        if arg == "--file":
            file = next(it)
        elif arg == "--group":
            groups.append(next(it))
        elif arg == "--key":
            key = next(it)
        elif arg == "--default":
            rest.append(("default", next(it)))
        elif arg == "--type":
            next(it)
        elif arg == "--delete":
            delete = True
        elif arg == "--notify":
            pass
        else:
            rest.append(("value", arg))
    section = "][".join(groups)
    return file, section, key, dict(rest), delete


def load(file: str) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # keep key case
    cp.read(CONFIG / file, encoding="utf-8")
    return cp


def kwrite(args: list[str]) -> int:
    if args == ["--help"]:
        print("Usage: kwriteconfig6 [--notify] --file f --group g --key k value")
        return 0
    file, section, key, rest, delete = parse_kconfig(args)
    cp = load(file)
    if delete:
        if cp.has_section(section):
            cp.remove_option(section, key)
    else:
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, key, rest.get("value", ""))
    with (CONFIG / file).open("w", encoding="utf-8") as fh:
        cp.write(fh, space_around_delimiters=False)
    log("kwriteconfig", file, section, key, "DELETE" if delete else rest.get("value", ""))
    return 0


def kread(args: list[str]) -> int:
    file, section, key, rest, _ = parse_kconfig(args)
    cp = load(file)
    if cp.has_option(section, key):
        print(cp.get(section, key))
    else:
        print(rest.get("default", ""))
    return 0


def dbus_send(args: list[str]) -> int:
    joined = " ".join(args)
    log("dbus-send", joined)
    if "NameHasOwner" in joined:
        print("   boolean true")
    elif "evaluateScript" in joined:
        script = next((a[len("string:"):] for a in args if a.startswith("string:")), "")
        if 'readConfig("Image"' in script:
            print("   file:///usr/share/wallpapers/Steam/steam.jpg")
        elif 'readConfig("launchers"' in script:
            print("   1:2=applications:steam.desktop,applications:org.kde.dolphin.desktop")
    return 0


def flatpak(args: list[str]) -> int:
    log("flatpak", *args)
    installed = set(FLATPAKS.read_text().split()) if FLATPAKS.exists() else set()
    if args[:1] == ["info"]:
        return 0 if args[1] in installed else 1
    if args[:1] == ["install"]:
        installed.add(args[-1])
        FLATPAKS.write_text("\n".join(sorted(installed)) + "\n")
    if args[:1] == ["list"]:
        print("\n".join(sorted(installed)))
    return 0


def apply_colorscheme(args: list[str]) -> int:
    log("plasma-apply-colorscheme", *args)
    return kwrite(["--file", "kdeglobals", "--group", "General", "--key", "ColorScheme", args[0]])


def apply_desktoptheme(args: list[str]) -> int:
    log("plasma-apply-desktoptheme", *args)
    return kwrite(["--file", "plasmarc", "--group", "Theme", "--key", "name", args[0]])


def main() -> int:
    tool, args = sys.argv[1], sys.argv[2:]
    handlers = {
        "kwriteconfig6": kwrite,
        "kreadconfig6": kread,
        "dbus-send": dbus_send,
        "flatpak": flatpak,
        "plasma-apply-colorscheme": apply_colorscheme,
        "plasma-apply-desktoptheme": apply_desktoptheme,
        "plasmashell": lambda a: print("plasmashell 6.4.3") or 0,
    }
    if tool not in handlers:
        log(tool, *args)
        return 0
    return handlers[tool](args)


if __name__ == "__main__":
    sys.exit(main())
