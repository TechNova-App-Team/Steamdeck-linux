// NovaDeck window helper for KWin (Plasma 6, with Plasma 5 fallbacks).
//
// The hub tells us through its window title that it just started an app:
// it renames itself to "NovaDeck · Start" for a few seconds. When a new app
// window shows up in that moment, we tuck the hub away and bring the app to
// the front. When every window opened that way is closed again, the hub
// comes back by itself, like on a console.

var HUB = /^NovaDeck(\s·\sStart)?(\s[-–—]\s.*)?$/;
var LAUNCHING = /^NovaDeck\s·\sStart/;
var IGNORE = /keyboard|tastatur/i;

var hubAway = false;   // we minimized the hub
var opened = [];       // windows the user opened while the hub was away

function allWindows() {
    return workspace.windowList ? workspace.windowList() : workspace.clientList();
}
function activeWindow() {
    return workspace.activeWindow !== undefined ? workspace.activeWindow : workspace.activeClient;
}
function activate(w) {
    if (workspace.activeWindow !== undefined) { workspace.activeWindow = w; } else { workspace.activeClient = w; }
}
function isHub(w) {
    return !!w && HUB.test(String(w.caption || ""));
}
function isApp(w) {
    return !!w && w.normalWindow && !w.skipTaskbar && !w.keepAbove && !isHub(w)
        && !IGNORE.test(String(w.caption || ""));
}
function findHub() {
    var list = allWindows();
    for (var i = 0; i < list.length; i++) {
        if (isHub(list[i])) { return list[i]; }
    }
    return null;
}
function forget(w) {
    for (var i = opened.length - 1; i >= 0; i--) {
        if (opened[i] === w) { opened.splice(i, 1); }
    }
}

function onAdded(w) {
    if (!isApp(w)) { return; }
    var hub = findHub();
    if (!hub) { return; }
    if (!hubAway && !hub.minimized && LAUNCHING.test(String(hub.caption || ""))) {
        hubAway = true;
        opened = [w];
        hub.minimized = true;
        activate(w);
    } else if (hubAway) {
        opened.push(w);  // e.g. Minecraft opening from the Prism Launcher
    }
}

function onRemoved(w) {
    if (!hubAway) { return; }
    forget(w);
    if (opened.length > 0) { return; }
    hubAway = false;
    var hub = findHub();
    if (hub && hub.minimized) {
        hub.minimized = false;
        activate(hub);
    }
}

function onActivated(w) {
    // The user brought the hub back on their own (e.g. via the panel): stop tracking.
    if (hubAway && isHub(w) && !w.minimized) {
        hubAway = false;
        opened = [];
    }
}

(workspace.windowAdded || workspace.clientAdded).connect(onAdded);
(workspace.windowRemoved || workspace.clientRemoved).connect(onRemoved);
(workspace.windowActivated || workspace.clientActivated).connect(onActivated);
