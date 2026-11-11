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
      textContent: "", hidden: false, value: "", dataset: {}, children: [], elements: {},
      classList: { toggle() {}, remove() {} },
      listeners: {},
      addEventListener(type, listener) { this.listeners[type] = listener; },
      reportValidity: () => true,
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
      const status = vm.runInContext(`statusText({phase: "${phase}", round: 2})`, context);
      assert.match(status.title, /WARTEN|PROMPT-ZEIT|BOT FERTIG|BOTS BEREIT|RUNDE GESCHLOSSEN|KAMPF!/);
      assert.doesNotMatch(status.label, /ROUND|CLOSED|MATCH/);
    }
    const ready = vm.runInContext('statusText({phase: "ready", round: 2})', context);
    assert.doesNotMatch(ready.title, /GEWONNEN/);
    const demo = vm.runInContext('statusText({phase: "fighting", demo: true})', context);
    assert.equal(demo.title, "SCHAU ZU!");
  });
}

test("teams see only their own score next to their name", () => {
  const { context, elements } = page("server/static/team.js");
  vm.runInContext('renderStage = renderStartChoice = renderAgent = renderCode = () => {};', context);
  for (const points of [0, 1, 2]) {
    vm.runInContext(`renderState({phase: "setup", round: 0, team: {name: "Team <A>", points: ${points}}, prompt: "", max_prompt_chars: 2000, prompt_open: false})`, context);
    assert.equal(elements.get("team-points").textContent, `score:${points}`);
    assert.equal(elements.get("team-name").textContent, "Team <A>");
    assert.equal(elements.has("scores-list"), false);
  }
  const html = fs.readFileSync(path.join(__dirname, "..", "server/static/team.html"), "utf8");
  assert.match(html, /<header[\s\S]*id="team-points"[\s\S]*id="team-name"[\s\S]*<\/header>/);
  assert.doesNotMatch(html, /EURE PUNKTE|points-panel|Die Spielleitung vergibt eure Punkte manuell/);
});

test("admin manual points table preserves names and disables subtraction at zero", () => {
  const { context } = page("server/admin_static/admin.js");
  const row = vm.runInContext('pointsRow({id: 1, name: "Team <A>", points: 0})', context);
  assert.equal(row.children[0].textContent, "Team <A>");
  assert.equal(row.children[1].textContent, "0");
  assert.equal(row.children[2].children[0].textContent, "+1 PUNKT");
  assert.equal(row.children[2].children[1].disabled, true);
  assert.equal(vm.runInContext('pointsRow({id: 1, name: "Team A", points: 1}).children[2].children[1].disabled', context), false);
});

test("ranking component is removed from all pages", () => {
  for (const file of ["server/admin_static/admin.html", "server/static/team.html", "server/static/chat.html"]) {
    const html = fs.readFileSync(path.join(__dirname, "..", file), "utf8");
    assert.doesNotMatch(html, /RANGLISTE|scores-/);
  }
});

test("admin translates display statuses without changing protocol values", () => {
  const { context } = page("server/admin_static/admin.js");
  assert.equal(vm.runInContext("PHASE_TITLES.prompting", context), "PROMPTS OFFEN");
  assert.equal(vm.runInContext("STATUS_LABELS.failed", context), "FEHLGESCHLAGEN");
  assert.equal(vm.runInContext("STATUS_LABELS.finished", context), "BEENDET");
  assert.equal(vm.runInContext("checkLine({status: 'pass', name: 'Verbindung'}).children[0].dataset.status", context), "pass");
  assert.equal(vm.runInContext("checkLine({status: 'pass', name: 'Verbindung'}).children[0].textContent", context), "BESTANDEN");
});

test("admin fight time sits next to start match and is sent in seconds", () => {
  const { context, elements } = page("server/admin_static/admin.js");
  vm.runInContext(`
    state = {stages: {BATTLEFIELD: {id: "BATTLEFIELD", name: "Battlefield"}}};
    fillSettings({time_limit_seconds: 300});
  `, context);
  assert.equal(elements.get("match-minutes").value, 5);
  elements.get("match-minutes").value = "3.5";
  elements.get("round-stage").value = "BATTLEFIELD";
  vm.runInContext("api = (method, path, body) => { globalThis.request = {method, path, body}; };", context);
  elements.get("start-match").onclick();
  assert.equal(context.request.path, "/api/match/start");
  assert.equal(context.request.body.time_limit_seconds, 210);
  assert.equal(context.request.body.demo, false);
  elements.get("match-minutes").value = "1";
  elements.get("start-match").onclick();
  assert.equal(context.request.body.time_limit_seconds, 60);
  elements.get("match-minutes").value = "3.5";
  elements.get("demo-match").onclick();
  assert.equal(context.request.body.demo, true);
  assert.equal(context.request.body.time_limit_seconds, 210);
  context.request = null;
  elements.get("match-minutes").reportValidity = () => false;
  elements.get("start-match").onclick();
  assert.equal(context.request, null);
  for (const phase of ["setup", "prompting", "coding", "ready", "fighting"]) {
    vm.runInContext(`state.phase = "${phase}"; updateButtons();`, context);
    assert.equal(elements.get("match-minutes").disabled, !["setup", "ready"].includes(phase));
  }
  const html = fs.readFileSync(path.join(__dirname, "..", "server/admin_static/admin.html"), "utf8");
  assert.match(html, /Maximale Kampf-Zeit \(Minuten\)<input[^>]*id="match-minutes"[^>]*value="5"[^>]*><\/label>\s*<button[^>]*id="start-match"/);
  assert.doesNotMatch(html, /round-fight-minutes/);
  assert.doesNotMatch(html, /name="time_limit_seconds"/);
});
