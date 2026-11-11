// Runs novadeck/kwin/novadeck-helper.js against a fake KWin workspace.
// Usage: node tests/kwin_helper_test.js
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync(path.join(__dirname, '..', 'novadeck', 'kwin', 'novadeck-helper.js'), 'utf8');
const focusSource = fs.readFileSync(path.join(__dirname, '..', 'novadeck', 'kwin', 'novadeck-focus.js'), 'utf8');

function signal() {
  const handlers = [];
  return { connect: (fn) => handlers.push(fn), emit: (...a) => handlers.forEach((fn) => fn(...a)) };
}

function makeWorkspace(plasma6) {
  const windows = [];
  const ws = plasma6
    ? { windowList: () => windows.slice(), activeWindow: null, windowAdded: signal(), windowRemoved: signal(), windowActivated: signal() }
    : { clientList: () => windows.slice(), activeClient: null, clientAdded: signal(), clientRemoved: signal(), clientActivated: signal() };
  const added = plasma6 ? ws.windowAdded : ws.clientAdded;
  const removed = plasma6 ? ws.windowRemoved : ws.clientRemoved;
  const activated = plasma6 ? ws.windowActivated : ws.clientActivated;
  const activeKey = plasma6 ? 'activeWindow' : 'activeClient';
  return {
    ws,
    open(props) {
      const w = { caption: '', normalWindow: true, skipTaskbar: false, keepAbove: false, minimized: false, ...props };
      windows.push(w);
      added.emit(w);
      return w;
    },
    close(w) {
      windows.splice(windows.indexOf(w), 1);
      removed.emit(w);
    },
    activate(w) {
      ws[activeKey] = w;
      activated.emit(w);
    },
    active: () => ws[activeKey],
  };
}

function load(env, src = source) {
  vm.runInNewContext(src, { workspace: env.ws });
}

let checks = 0;
function check(name, fn) {
  fn();
  checks += 1;
  console.log(`  ok   ${name}`);
}

for (const plasma6 of [true, false]) {
  const label = plasma6 ? 'Plasma 6' : 'Plasma 5';

  check(`${label}: app from the hub hides the hub, closing it brings the hub back`, () => {
    const env = makeWorkspace(plasma6);
    load(env);
    const hub = env.open({ caption: 'NovaDeck' });
    env.activate(hub);
    hub.caption = 'NovaDeck · Start';
    const netflix = env.open({ caption: 'Netflix' });
    assert.strictEqual(hub.minimized, true);
    assert.strictEqual(env.active(), netflix);
    hub.caption = 'NovaDeck';
    env.close(netflix);
    assert.strictEqual(hub.minimized, false);
    assert.strictEqual(env.active(), hub);
  });

  check(`${label}: hub waits until every window opened meanwhile is closed`, () => {
    const env = makeWorkspace(plasma6);
    load(env);
    const hub = env.open({ caption: 'NovaDeck' });
    hub.caption = 'NovaDeck · Start';
    const prism = env.open({ caption: 'Prism Launcher' });
    hub.caption = 'NovaDeck';
    const minecraft = env.open({ caption: 'Minecraft 1.21' });
    env.close(prism);
    assert.strictEqual(hub.minimized, true, 'Minecraft still open');
    env.close(minecraft);
    assert.strictEqual(hub.minimized, false);
  });

  check(`${label}: windows that pop up on their own do not hide the hub`, () => {
    const env = makeWorkspace(plasma6);
    load(env);
    const hub = env.open({ caption: 'NovaDeck' });
    env.open({ caption: 'Discord Updater' });
    assert.strictEqual(hub.minimized, false);
  });

  check(`${label}: on-screen keyboard and tool windows are ignored`, () => {
    const env = makeWorkspace(plasma6);
    load(env);
    const hub = env.open({ caption: 'NovaDeck · Start' });
    env.open({ caption: 'Steam Keyboard' });
    env.open({ caption: 'Overlay', skipTaskbar: true });
    env.open({ caption: 'Popup', normalWindow: false });
    assert.strictEqual(hub.minimized, false);
  });

  check(`${label}: restoring the hub manually stops the automatic return`, () => {
    const env = makeWorkspace(plasma6);
    load(env);
    const hub = env.open({ caption: 'NovaDeck · Start' });
    const yt = env.open({ caption: 'YouTube' });
    hub.caption = 'NovaDeck';
    hub.minimized = false;
    env.activate(hub);
    const other = env.open({ caption: 'Dolphin' });
    env.close(yt);
    assert.strictEqual(hub.minimized, false);
    assert.strictEqual(other.minimized, false);
  });

  check(`${label}: focus script brings a minimized hub to the front`, () => {
    const env = makeWorkspace(plasma6);
    const hub = env.open({ caption: 'NovaDeck', minimized: true });
    env.open({ caption: 'Dolphin' });
    load(env, focusSource);
    assert.strictEqual(hub.minimized, false);
    assert.strictEqual(env.active(), hub);
  });
}

console.log(`all ${checks} KWin helper checks passed`);
