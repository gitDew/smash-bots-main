// UI text checks without a browser or third-party dependencies.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

test("admin hides settings by default and omits the removed explanations", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "server/admin_static/admin.html"), "utf8");
  assert.doesNotMatch(html, /Die Kinder sehen die Karte auf ihren Handys/);
  assert.doesNotMatch(html, /Ein Code für alle\./);
  assert.doesNotMatch(html, /Wähle oben in der Tabelle 2 bis 4 Teams/);
  assert.match(html, /<details class="panel help-sheet" id="settings-panel">\s*<summary>EINSTELLUNGEN<\/summary>[\s\S]*<form id="settings"[\s\S]*<\/details>/);
});

function page(script) {
  const elements = new Map();
  function element() {
    return {
      textContent: "", hidden: false, value: "", dataset: {}, children: [],
      classList: { toggle() {}, remove() {} },
      addEventListener() {},
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
    };
  }
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      createElement: element,
      querySelectorAll: () => [],
    },
    location: { pathname: "/t/test", protocol: "http:", host: "localhost" },
    localStorage: { getItem: () => null },
    WebSocket: class {},
    fetch: async () => ({ text: async () => "" }),
    setInterval() {},
  });
  for (const file of ["server/static/common.js", script]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", file), "utf8"), context);
  }
  return { context, elements };
}

for (const script of ["server/static/team.js", "server/static/chat.js"]) {
  test(`${script}: all round statuses are German`, () => {
    const { context } = page(script);
    for (const phase of ["setup", "prompting", "coding", "ready", "fighting"]) {
      const status = vm.runInContext(`statusText({phase: "${phase}", round: 2, scores: {last: null}})`, context);
      assert.match(status.title, /WARTEN|PROMPT-ZEIT|BOT FERTIG|BOTS BEREIT|RUNDE GESCHLOSSEN|KAMPF!/);
      assert.doesNotMatch(status.label, /ROUND|CLOSED|MATCH/);
    }
    const winner = vm.runInContext('statusText({phase: "ready", round: 2, scores: {last: {round: 2, winner: "Team A", won: true}}})', context);
    assert.match(winner.title, /GEWONNEN/);
    const demo = vm.runInContext('statusText({phase: "fighting", demo: true})', context);
    assert.equal(demo.title, "SCHAU ZU!");
  });
}

test("scoreboard uses German singular and plural and preserves team names", () => {
  const { context, elements } = page("server/static/chat.js");
  vm.runInContext('renderScores({last: null, table: [{name: "Team <A>", wins: 1, played: 1}, {name: "Team B", wins: 2, played: 3}]})', context);
  const rows = elements.get("scores-list").children;
  assert.equal(rows[0].children[0].textContent, "Team <A>");
  assert.equal(rows[0].children[1], ": 1 Sieg in 1 Kampf");
  assert.equal(rows[1].children[1], ": 2 Siege in 3 Kämpfen");
  assert.equal(elements.get("scores-last").textContent, "Noch kein Kampf wurde gewertet.");
});

test("admin translates display statuses without changing protocol values", () => {
  const { context } = page("server/admin_static/admin.js");
  assert.equal(vm.runInContext("PHASE_TITLES.prompting", context), "PROMPTS OFFEN");
  assert.equal(vm.runInContext("STATUS_LABELS.failed", context), "FEHLGESCHLAGEN");
  assert.equal(vm.runInContext("STATUS_LABELS.finished", context), "BEENDET");
  assert.equal(vm.runInContext("checkLine({status: 'pass', name: 'Verbindung'}).children[0].dataset.status", context), "pass");
  assert.equal(vm.runInContext("checkLine({status: 'pass', name: 'Verbindung'}).children[0].textContent", context), "BESTANDEN");
});
