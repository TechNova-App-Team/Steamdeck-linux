// One-shot KWin script: bring an already open NovaDeck hub to the front.
(function () {
    var HUB = /^NovaDeck(\s·\sStart)?(\s[-–—]\s.*)?$/;
    var list = workspace.windowList ? workspace.windowList() : workspace.clientList();
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        if (HUB.test(String(w.caption || ""))) {
            w.minimized = false;
            if (workspace.activeWindow !== undefined) { workspace.activeWindow = w; } else { workspace.activeClient = w; }
            return;
        }
    }
})();
