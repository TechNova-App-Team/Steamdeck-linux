# ✦ NovaDeck

**Dein Steam-Deck-Desktop, aufgeräumt.**
NovaDeck macht aus dem Desktop-Modus des Steam Deck eine saubere, per Controller bedienbare Oberfläche im Glas-Look: schwarz, grau, neon-weiß. Minecraft, Fortnite, Netflix, YouTube und Spotify liegen einen Knopfdruck entfernt, ohne Umwege über Menüs, Browser-Tabs oder Terminal.

Der **Gaming-Modus (Big Picture) bleibt genau so, wie er ist.**

![NovaDeck Hub](docs/screenshots/hub.jpg)

---

## Was du bekommst

| | |
|---|---|
| **NovaDeck Hub** | Ein Vollbild-Startbildschirm wie bei einer Konsole. Bedienung mit Steuerkreuz, A und B, per Touchscreen oder Trackpad. Zeigt Uhrzeit, Akku, WLAN, Lautstärke und freien Speicher. |
| **Apps als echte Apps** | Netflix, YouTube, Prime Video, Disney+, Twitch, Crunchyroll, YouTube Music, Fortnite (Cloud), Xbox Cloud und GeForce NOW öffnen sich als eigene App-Fenster ohne Browser-Leisten. |
| **Ein-Klick-Installation** | Fehlt eine App (z. B. Spotify oder Discord), drückst du A auf der Kachel und NovaDeck installiert sie aus Flathub, ohne Passwort. |
| **Glas-Design für den ganzen Desktop** | Farbschema „Nova Glass“, Glas-Wallpaper, durchscheinende Leiste mit Blur, eigener Startbildschirm beim Wechsel in den Desktop-Modus, Terminal im gleichen Look. |
| **Aufgeräumte Leiste** | Unten nur noch das Wichtige: NovaDeck, Steam, Browser, Dateien, App Store, Tastatur, Einstellungen. |
| **Schnellmenü** | Lautstärke, Helligkeit, Speicheranzeige, eigene Kacheln, Ausgeblendetes, Einstellungen. |
| **Ein/Aus-Menü** | Zurück in den Gaming-Modus, Standby, Neustart und Ausschalten, alles mit dem Controller. |
| **Eigene Kacheln** | Jede Website als App-Kachel: ARD Mediathek, Joyn, WhatsApp Web und alles andere. |
| **Nichts geht kaputt** | Alles liegt in deinem Home-Ordner und übersteht SteamOS-Updates. Kein `sudo`, kein Entsperren des Systems. Jede Änderung lässt sich mit einem Klick rückgängig machen. |

<p>
  <img src="docs/screenshots/options.jpg" width="49%" alt="Optionen einer Kachel">
  <img src="docs/screenshots/quick-menu.jpg" width="49%" alt="Schnellmenü">
</p>

---

## Installation (ca. 5 Minuten)

### 1. In den Desktop-Modus wechseln
Steam-Taste → **Ein/Aus** → **Zum Desktop wechseln**.

### 2. NovaDeck herunterladen
Dieses Repository ist privat, du brauchst also deinen GitHub-Login.

**Variante A: als ZIP (am einfachsten)**
1. Diese Seite auf dem Deck öffnen und anmelden.
2. Grüner Button **Code** → **Download ZIP**.
3. Im Dateimanager (Dolphin) unter *Downloads*: Rechtsklick (L2) auf die ZIP → **Entpacken → Hierher entpacken**.

**Variante B: mit Konsole**
```bash
git clone https://github.com/TechNova-App-Team/Steamdeck-linux.git ~/NovaDeck
```
Git fragt nach Benutzername und Passwort. Als Passwort nimmst du ein [Personal Access Token](https://github.com/settings/tokens), nicht dein GitHub-Passwort.

### 3. Installer starten
Im entpackten Ordner auf **`install.sh`** tippen und **Ausführen** wählen. Ein Terminalfenster öffnet sich automatisch.

Passiert nichts, fehlt nach dem Entpacken das Ausführen-Recht: Rechtsklick auf `install.sh` → **Eigenschaften → Berechtigungen → „Ausführbar“** anhaken. Oder einfach die Konsole nehmen (siehe unten).

Alternativ in der Konsole:
```bash
cd ~/NovaDeck && bash install.sh
```

Es erscheint eine Liste, in der du auswählst, was eingerichtet wird:

- ☑ Glas-Design für den Desktop
- ☑ NovaDeck beim Wechsel in den Desktop-Modus automatisch öffnen
- ☑ Google Chrome (für Netflix, YouTube, Fortnite-Cloud & Co.)
- ☑ Prism Launcher (Minecraft)
- ☐ Spotify, Discord, Heroic, VLC
- ☐ Bildschirmsperre im Desktop-Modus abschalten

Alles, was du hier weglässt, kannst du später per Knopfdruck im Hub nachinstallieren.

> **Tipp:** Wechsle nach der Installation einmal in den Gaming-Modus und zurück. Dann ist das Design überall aktiv, auch in schon geöffneten Programmen.

---

## Bedienung

Steam leitet im Desktop-Modus die Tasten des Decks als Tastatur und Maus weiter. NovaDeck ist genau darauf abgestimmt.

| Deck | Tastatur | Im Hub |
|---|---|---|
| Steuerkreuz | Pfeiltasten | Kacheln auswählen |
| **A** | Enter | Starten / Bestätigen |
| **B** | Esc | Zurück / Schließen |
| **L2** (Rechtsklick) | `X` / Kontextmenü-Taste | Optionen: Favorit, Ausblenden, Zu Steam hinzufügen |
| Touchscreen, rechtes Trackpad | Maus | Antippen = Starten |
| **Steam + X** | | Bildschirmtastatur |
| | `Tab` / `Q` `E` | Nächste / vorherige Reihe |
| | `O` | Schnellmenü |
| | `P` | Ein/Aus-Menü |

Ist ein Controller direkt im Browser sichtbar (z. B. ein externes Gamepad ohne Steam), funktionieren zusätzlich **X** (Optionen), **Y** (Favorit), **LB/RB** (Reihe wechseln), **Start** (Schnellmenü) und **Select** (Ein/Aus).

Den Hub öffnest du jederzeit über das **NovaDeck-Icon** in der Leiste oder auf dem Desktop.

![Ein/Aus-Menü](docs/screenshots/power.jpg)

---

## Gut zu wissen

### Fortnite
Fortnite läuft unter Linux nicht direkt, weil Epic den Anti-Cheat für Linux nicht freischaltet. Daran kann keine App etwas ändern. **Über die Cloud klappt es trotzdem:**

- **Xbox Cloud Gaming** (Kachel *Fortnite*): kostenlos, du brauchst nur ein Microsoft-Konto.
- **GeForce NOW**: kostenlose Stufe mit Warteschlange, verbindet sich mit deinem Epic-Konto.

**Mit Controller spielen:** Im Desktop-Modus macht Steam aus dem Controller Maus und Tastatur. Für echte Controller-Steuerung startest du Fortnite am besten aus dem Gaming-Modus:

1. Im Hub auf *Fortnite*: **L2 → Zu Steam hinzufügen**.
2. In der Liste „Fortnite“ anhaken → **Ausgewählte Programme hinzufügen**.
3. Im Gaming-Modus findest du Fortnite jetzt unter *Nicht-Steam*. NovaDeck erkennt den Gaming-Modus und startet es dort im Vollbild.

Das funktioniert genauso mit Netflix, YouTube und allen anderen Kacheln.

### Minecraft
Die Kachel startet den **Prism Launcher**, den besten Minecraft-Launcher fürs Deck. Du meldest dich einmal mit deinem Microsoft-Konto an (Minecraft Java Edition muss gekauft sein) und wählst eine Version. Für Controller-Steuerung installierst du im Launcher die Mod **Controlify** oder fügst den Prism Launcher wie oben beschrieben zu Steam hinzu.

### Netflix & Co.
Die Streaming-Kacheln öffnen sich in Google Chrome als eigenes Fenster ohne Leisten. Du meldest dich einmal an und bleibst eingeloggt. Netflix streamt unter Linux in maximal 720p, für den 800p-Bildschirm des Decks reicht das.

### Eigene Kacheln
Im Hub auf **Kachel hinzufügen** (Ende der Favoriten-Reihe) oder im Schnellmenü. Name und Adresse eintippen (die Bildschirmtastatur öffnest du mit dem Tastatur-Knopf oder Steam + X), fertig. Eigene Kacheln landen auch im App-Menü und lassen sich so ebenfalls zu Steam hinzufügen.

---

## Was genau wird verändert?

NovaDeck schreibt nur in deinen Home-Ordner:

| Was | Wo |
|---|---|
| Programm | `~/.local/share/novadeck/` |
| App-Starter, Autostart | `~/.local/share/applications/novadeck-*.desktop`, `~/.config/autostart/novadeck-hub.desktop` |
| Design | Farbschema, Konsole-Profil, Startbildschirm unter `~/.local/share/` |
| Deine Einstellungen im Hub | `~/.config/novadeck/state.json` |
| Sicherung deiner alten KDE-Einstellungen | `~/.config/novadeck/backup/<Datum>/` |
| Liste aller geänderten Werte (für die Wiederherstellung) | `~/.config/novadeck/restore.tsv` |

Apps werden per Flatpak **für deinen Nutzer** installiert (wie im App Store Discover) und überstehen SteamOS-Updates. Für Google Chrome setzt NovaDeck außerdem zwei Dinge: Zugriff auf Controller für Cloud-Gaming (dasselbe macht Valve für Chrome) und, nur bei einem frischen Chrome, das Abschalten der lästigen KDE-Wallet-Passwortabfrage.

---

## Entfernen

App-Menü → **NovaDeck entfernen**. Oder in der Konsole:
```bash
~/.local/share/novadeck/uninstall.sh
```
Dein vorheriges Farbschema, Wallpaper, die Leiste und alle anderen Einstellungen kommen zurück. Installierte Apps wie Chrome oder der Prism Launcher bleiben; die entfernst du bei Bedarf im App Store.

---

## Probleme?

```bash
python3 ~/.local/share/novadeck/app/novadeck.py doctor
```
zeigt, was NovaDeck auf deinem Deck findet (Browser, Steam, Lautstärke, Helligkeit …). Protokolle liegen in `~/.local/state/novadeck/`.

| Problem | Lösung |
|---|---|
| Hub öffnet sich nicht | Prüfen, ob Google Chrome installiert ist (`doctor`). Ohne Chrome öffnet sich der Hub im Standard-Browser. |
| Eine gestartete App erscheint hinter dem Hub | Im Schnellmenü „Hub beim Start schließen“ einschalten (Standard). |
| Steam-Tastatur erscheint nicht | Steam muss im Desktop-Modus laufen. Notfalls Steam über das Icon in der Leiste neu starten. |
| Design nur teilweise sichtbar | Einmal in den Gaming-Modus und zurück wechseln. |

---

## Für Entwickler

```
novadeck/            Hub: Python-Dienst (nur Standardbibliothek) + Web-Oberfläche
  novadeck.py        Server, Launcher, CLI (hub, launch, doctor, …)
  catalog.json       Alle Kacheln
  web/               index.html, css/, js/, icons.json, img/, fonts/
theme/               Farbschema, Konsole, Startbildschirm (Plasma Look-and-Feel)
assets/icons/        App-Icons (generiert)
tools/build_assets.py  erzeugt Wallpaper, Icons, Splash-Bilder (numpy + Pillow)
tests/               Unit-/API-Tests und Installer-Test mit simulierten KDE-Tools
```

Hub ohne Steam Deck ausprobieren (Demo-Modus, startet nichts wirklich):
```bash
NOVADECK_DEMO=1 python3 novadeck/novadeck.py serve
# dann http://127.0.0.1:47820/ im Browser öffnen
```

Tests:
```bash
python3 -m unittest discover -s tests -v
bash tests/test_install.sh
shellcheck install.sh uninstall.sh
```

Neue Kachel: Eintrag in `novadeck/catalog.json` ergänzen (`kind`: `flatpak`, `web`, `command`, `uri` oder `browser`). Neues Icon: Pfad in `novadeck/web/icons.json` eintragen und `python3 tools/build_assets.py` ausführen.

---

Schrift: [Inter](https://rsms.me/inter/) (SIL Open Font License). NovaDeck steht in keiner Verbindung zu Valve, Netflix, Epic Games, Microsoft oder anderen genannten Diensten; deren Namen dienen nur der Beschriftung der Kacheln.
