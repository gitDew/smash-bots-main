"use strict";

// Team names, prompts and chats are written by kids or the AI: always insert as text.

const PHASE_TITLES = {
  setup: "VORBEREITUNG", prompting: "PROMPTS OFFEN", coding: "KI PROGRAMMIERT", ready: "BOTS BEREIT", fighting: "KAMPF LÄUFT",
};
const STATUS_LABELS = {
  queued: "WARTET", running: "LÄUFT", done: "FERTIG", failed: "FEHLGESCHLAGEN",
  skipped: "ÜBERSPRUNGEN", finished: "BEENDET", stopped: "GESTOPPT",
  pass: "BESTANDEN", fail: "FEHLGESCHLAGEN", warn: "WARNUNG",
};
const MOODS = { setup: "wait", prompting: "open", coding: "closed", ready: "closed", fighting: "fight" };

let state = null;
let settingsFilled = false;
let roundFormFilled = false;
const clock = createClock($("status-clock"));
const selected = new Set();
const feeds = new Map();
let characterTeamId = null;

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function api(method, path, body, raw = false) {
  $("error").textContent = "";
  try {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": raw ? "application/octet-stream" : "application/json", "X-Smash-Admin": "1" },
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = typeof data.detail === "string" ? data.detail : "Das wurde nicht akzeptiert. Prüfe die Werte.";
      $("error").textContent = detail;
      window.scrollTo({ top: 0 });
      return null;
    }
    return await response.json();
  } catch {
    $("error").textContent = "Der Server konnte nicht erreicht werden.";
    return null;
  }
}

function connect() {
  const socket = new WebSocket(`ws://${location.host}/ws`);
  socket.onopen = () => { $("connection").hidden = true; };
  socket.onclose = () => { $("connection").hidden = false; setTimeout(connect, 1000); };
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") render(message);
    if (message.type === "match_log") appendLog(message.line);
    if (message.type === "agent_activity") addActivity(message.entry);
    if (message.type === "agent_activity_history") {
      feeds.forEach((feed) => { feed.list.replaceChildren(); feed.round = null; });
      message.entries.forEach(addActivity);
    }
  };
}

function syncFeeds(teams) {
  const known = new Set(teams.map((team) => team.id));
  feeds.forEach((feed, id) => {
    if (!known.has(id)) { feed.root.remove(); feeds.delete(id); }
  });
  teams.forEach((team) => {
    if (!feeds.has(team.id)) {
      const title = el("h3");
      const list = el("div", { className: "feed-list" });
      const root = el("div", { className: "feed" }, title, list);
      $("feeds").append(root);
      feeds.set(team.id, { root, title, list, round: null });
    }
    feeds.get(team.id).title.textContent = team.name;
  });
}

function addActivity(entry) {
  const feed = feeds.get(entry.team_id);
  if (!feed) return;
  if (feed.round !== entry.round) {
    feed.list.replaceChildren();
    feed.round = entry.round;
  }
  const { list } = feed;
  const following = list.scrollTop + list.clientHeight >= list.scrollHeight - 12;
  const line = el("div", { className: "feed-line" },
    el("span", { className: "feed-time", textContent: new Date(entry.time * 1000).toLocaleTimeString("de-CH") }),
    entry.label ? el("b", { textContent: entry.label }) : "",
  );
  line.dataset.tone = entry.tone;
  if (entry.detail) line.append(el("pre", { className: "feed-detail", textContent: entry.detail }));
  list.append(line);
  if (following) list.scrollTop = list.scrollHeight;
}

function appendLog(line) {
  const log = $("match-log");
  log.append(`${line}\n`);
  log.scrollTop = log.scrollHeight;
}

function button(text, className, onClick) {
  return el("button", { type: "button", className: `btn btn-small ${className}`, textContent: text, onclick: onClick });
}

function freeCharacters(keep = null) {
  const taken = new Set(state.teams.map((team) => team.character));
  return Object.entries(state.characters).filter(([name]) => name === keep || !taken.has(name));
}

function fillSelect(select, options, selected) {
  const signature = options.map(([value]) => value).join();
  if (select.dataset.options === signature) return;
  const current = selected ?? select.value;
  select.replaceChildren(...options.map(([value, label]) => el("option", { value, textContent: label })));
  select.dataset.options = signature;
  if (options.some(([value]) => value === current)) select.value = current;
}

function showCharacterDialog(team) {
  characterTeamId = team.id;
  $("character-title").textContent = team.name;
  delete $("character-select").dataset.options;
  fillSelect($("character-select"), freeCharacters(team.character), team.character);
  $("character-dialog").showModal();
}

function logoCell(team) {
  const input = el("input", {
    type: "file", accept: "image/png,image/jpeg,image/webp", hidden: true,
    ariaLabel: `Logo für ${team.name} hochladen`,
  });
  const upload = button(team.logo ? "LOGO ERSETZEN" : "LOGO HOCHLADEN", "", () => input.click());
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      $("error").textContent = "Das Logo darf höchstens 5 MB gross sein.";
      window.scrollTo({ top: 0 });
      input.value = "";
      return;
    }
    upload.disabled = true;
    upload.textContent = "WIRD HOCHGELADEN...";
    await api("PUT", `/api/teams/${team.id}/logo`, file, true);
    upload.disabled = false;
    upload.textContent = team.logo ? "LOGO ERSETZEN" : "LOGO HOCHLADEN";
    input.value = "";
  };
  const cell = el("td", {}, input);
  if (team.logo) cell.append(el("img", { className: "team-logo", src: team.logo, alt: `Logo von ${team.name}` }));
  cell.append(upload);
  if (team.logo) cell.append(button("LOGO ENTFERNEN", "", () => {
    if (window.confirm(`Logo von ${team.name} entfernen?`)) api("DELETE", `/api/teams/${team.id}/logo`);
  }));
  return cell;
}

function teamRow(team, phase) {
  const agent = team.agent;
  const agentCell = el("td");
  if (agent) {
    agentCell.append(el("span", { className: "pill", textContent: STATUS_LABELS[agent.status] || agent.status }));
    agentCell.lastChild.dataset.status = agent.status;
    const text = agent.step || agent.error || "";
    if (text) agentCell.append(el("div", { textContent: text }));
    if (agent.detail) agentCell.append(el("div", { className: "warn", textContent: agent.detail }));
  }
  const pick = el("input", { type: "checkbox", checked: selected.has(team.id), ariaLabel: `${team.name} für den Kampf auswählen` });
  pick.onchange = () => { if (pick.checked) selected.add(team.id); else selected.delete(team.id); updateButtons(); };

  const actions = el("td", { className: "row" });
  actions.append(
    button("QR", "", () => showQr(team.name, `/api/teams/${team.id}/qr`, team.link)),
    button("LINK KOPIEREN", "", () => navigator.clipboard.writeText(team.link)),
    button("DETAILS", "", () => showDetail(team.id)),
    button("UMBENENNEN", "", () => {
      const name = window.prompt("Neuer Teamname", team.name);
      if (name) api("PUT", `/api/teams/${team.id}`, { name });
    }),
    button("KI ERNEUT STARTEN", "btn-warn", () => api("POST", `/api/teams/${team.id}/retry`)),
    button("NEUER LINK", "btn-warn", () => {
      if (window.confirm(`Link für ${team.name} ersetzen? Der alte funktioniert danach nicht mehr.`)) api("POST", `/api/teams/${team.id}/regenerate`);
    }),
    button("LÖSCHEN", "btn-stop", () => {
      if (window.confirm(`${team.name}, den zugehörigen Container und alle Bot-Versionen löschen?`)) api("DELETE", `/api/teams/${team.id}`);
    }),
  );
  actions.querySelectorAll("button")[4].disabled = phase !== "ready";
  return el("tr", {},
    el("td", {}, pick),
    el("td", { textContent: team.name }),
    logoCell(team),
    el("td", {}, button(
      team.character ? state.characters[team.character] : "AUSWÄHLEN",
      team.character ? "" : "btn-warn",
      () => showCharacterDialog(team),
    )),
    el("td", { textContent: String(team.online) }),
    el("td", {},
      el("div", { className: "prompt-preview", textContent: team.prompt }),
      team.start_mode === "fresh" ? el("span", { className: "pill", textContent: "NEU ANFANGEN" }) : "",
    ),
    agentCell,
    actions,
  );
}

function updateButtons() {
  if (!state) return;
  const { phase } = state;
  const between = ["setup", "ready"].includes(phase);
  // While a round runs, the form keeps showing that round's choices.
  ["open-round", "round-minutes", "round-stage"].forEach((id) => { $(id).disabled = !between; });
  $("match-minutes").disabled = !between;
  $("close-round").disabled = phase !== "prompting";
  $("add-time").disabled = phase !== "prompting";
  $("start-match").disabled = !between || selected.size < 2 || selected.size > 4;
  $("demo-match").disabled = $("start-match").disabled;
  $("stop-match").disabled = phase !== "fighting";
  $("reset").disabled = ["coding", "fighting"].includes(phase);
}

function pointsRow(team) {
  const award = button("+1 PUNKT", "btn-go", () => api("POST", `/api/teams/${team.id}/points`, { delta: 1 }));
  award.ariaLabel = `Einen Punkt an ${team.name} vergeben`;
  const subtract = button("−1 PUNKT", "", () => api("POST", `/api/teams/${team.id}/points`, { delta: -1 }));
  subtract.ariaLabel = `Einen Punkt von ${team.name} abziehen`;
  subtract.disabled = team.points === 0;
  return el("tr", {},
    el("td", { textContent: team.name }),
    el("td", { textContent: String(team.points), ariaLive: "polite" }),
    el("td", { className: "row" }, award, subtract),
  );
}

function fillRoundForm(s) {
  const stages = Object.values(s.stages);
  $("round-stage").replaceChildren(...stages.map((stage) => el("option", { value: stage.id, textContent: stage.name })));
  $("round-stage").value = s.stage in s.stages ? s.stage : stages[0].id;
  // To the nearest half minute, which is what the box accepts.
  $("round-minutes").value = Math.max(0.5, Math.round(s.settings.prompt_seconds / 30) / 2);
  showPickedStage();
  roundFormFilled = true;
}

function pickedStage() {
  return state.stages[$("round-stage").value];
}

function showPickedStage() {
  const stage = pickedStage();
  $("round-stage-map").src = stage.image;
  $("round-stage-map").alt = `Karte von ${stage.name}`;
  $("round-stage-about").textContent = stage.about;
}

function showStageDialog() {
  const stage = pickedStage();
  $("stage-dialog-title").textContent = stage.name.toUpperCase();
  $("stage-dialog-map").src = stage.image;
  $("stage-dialog-map").alt = `Karte von ${stage.name}`;
  $("stage-dialog-about").textContent = stage.about;
  $("stage-dialog").showModal();
}

function matchStageText(s) {
  if (s.stages[s.stage]) return `Die Kämpfe dieser Runde finden auf ${s.stages[s.stage].name} statt.`;
  return "Vor der ersten Runde finden Kämpfe auf der unter NÄCHSTE RUNDE ausgewählten Bühne statt.";
}

function renderReplays(replays) {
  $("replays").replaceChildren(...(replays.length ? replays.map((replay) => el("a", {
    className: "btn", textContent: `VIDEO ANSEHEN · ${replay.name}`,
    href: replay.url, target: "_blank", rel: "noopener noreferrer",
  })) : [el("p", { textContent: "Noch keine Kampf-Videos gespeichert." })]));
}

function render(s) {
  state = s;
  $("status").dataset.mood = MOODS[s.phase];
  const roundStage = s.round && s.stages[s.stage] ? ` · ${s.stages[s.stage].name.toUpperCase()}` : "";
  $("status-label").textContent = s.round ? `RUNDE ${s.round}${roundStage}` : "VOR RUNDE 1";
  $("status-title").textContent = s.demo ? "DEMO LÄUFT" : PHASE_TITLES[s.phase];
  clock.set(s.remaining);

  const problems = [];
  if (!s.health.llm) problems.push("AZURE: BASIS-URL UND API-SCHLÜSSEL IN DEN EINSTELLUNGEN EINGEBEN");
  $("azure-key").placeholder = s.health.key_saved ? "Gespeichert. Zum Ersetzen neuen Schlüssel eingeben" : "Schlüssel einfügen";
  if (s.health.docker !== "ok") problems.push("SANDBOX-IMAGE FEHLT");
  $("health").textContent = problems.join(" / ");
  $("health").hidden = problems.length === 0;

  const known = new Set(s.teams.map((team) => team.id));
  [...selected].forEach((id) => { if (!known.has(id)) selected.delete(id); });
  $("teams").replaceChildren(...s.teams.map((team) => teamRow(team, s.phase)));
  fillSelect($("team-character-input"), [["", "Figur auswählen"], ...freeCharacters()]);
  syncFeeds(s.teams);

  $("match-log").textContent = s.match_log.length ? `${s.match_log.join("\n")}\n` : "";
  $("match-log").scrollTop = $("match-log").scrollHeight;
  $("matches").replaceChildren(...s.matches.map((match) => el("p", {
    textContent: `Runde ${match.round}${match.stage ? ` auf ${match.stage}` : ""}: ${match.teams.join(" gegen ")}. ${STATUS_LABELS[match.status] || match.status}`,
  })));
  $("match-stage").textContent = matchStageText(s);
  renderReplays(s.replays || []);
  $("points-teams").replaceChildren(...s.teams.map(pointsRow));

  $("chat-stats").textContent = `${s.chat.phones} ${s.chat.phones === 1 ? "Handy verbunden" : "Handys verbunden"}. ${s.chat.questions} ${s.chat.questions === 1 ? "Frage" : "Fragen"} in dieser Runde beantwortet.`;
  if (!settingsFilled) fillSettings(s.settings);
  if (!roundFormFilled) fillRoundForm(s);
  updateButtons();
}

function fillSettings(settings) {
  $("match-minutes").value = settings.time_limit_seconds > 0
    ? Math.max(0.5, Math.round(settings.time_limit_seconds / 30) / 2) : 5;
  const form = $("settings");
  for (const [key, value] of Object.entries(settings)) {
    const input = form.elements[key];
    if (!input) continue;
    if (input.type === "checkbox") input.checked = value; else input.value = value;
  }
  settingsFilled = true;
}

function showQr(title, source, link) {
  $("qr-title").textContent = title;
  $("qr-image").src = `${source}?${Date.now()}`;
  $("qr-link").textContent = link;
  $("qr-dialog").showModal();
}

async function showChats() {
  const data = await api("GET", "/api/chat/log");
  if (!data) return;
  $("detail-title").textContent = "HILFE-CHATS";
  $("detail-body").replaceChildren(...data.messages.map((message) => el("p", {
    textContent: `[${message.session.slice(0, 4)}] Runde ${message.round} ${message.role === "user" ? "Kind" : "Hilfe-KI"}: ${message.content}`,
  })));
  $("detail-dialog").showModal();
}

async function showDetail(teamId) {
  const detail = await api("GET", `/api/teams/${teamId}/detail`);
  if (!detail) return;
  $("detail-title").textContent = detail.name;
  const body = $("detail-body");
  body.replaceChildren(el("h3", { textContent: "PROMPTS" }));
  detail.prompts.forEach((prompt) => body.append(el("p", { textContent: `Runde ${prompt.round}: ${prompt.text}` })));
  body.append(el("h3", { textContent: "BOT-VERSIONEN" }));
  detail.versions.forEach((version) => {
    const roll = button("DIESE VERSION WIEDERHERSTELLEN", "btn-warn", async () => {
      if (window.confirm("Diese Version als aktuellen Bot des Teams verwenden?")) {
        await api("POST", `/api/teams/${teamId}/rollback`, { version_id: version.id });
        $("detail-dialog").close();
      }
    });
    body.append(el("details", {},
      el("summary", { textContent: `Version ${version.id}: ${version.note}` }),
      el("pre", { textContent: version.code }),
      roll,
    ));
  });
  $("detail-dialog").showModal();
}

document.querySelectorAll("[data-close]").forEach((closeButton) => {
  closeButton.onclick = () => closeButton.closest("dialog").close();
});

$("add-team").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("team-name-input").value.trim();
  const character = $("team-character-input").value;
  if (!name || !character) {
    $("error").textContent = "Ein neues Team braucht einen Namen und eine Figur.";
    return;
  }
  if (await api("POST", "/api/teams", { name, character })) $("team-name-input").value = "";
});

$("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const values = {};
  for (const input of form.querySelectorAll("input")) {
    // A saved key is never shown, so an empty box means "keep it".
    if (input.type === "password") { if (input.value.trim()) values[input.name] = input.value.trim(); }
    else if (input.type === "checkbox") values[input.name] = input.checked;
    else if (input.type === "number") values[input.name] = Number(input.value);
    else values[input.name] = input.value.trim();
  }
  if (await api("PUT", "/api/settings", values)) {
    $("azure-key").value = "";
    $("settings-saved").textContent = "Gespeichert";
    setTimeout(() => { $("settings-saved").textContent = ""; }, 2000);
  }
});

$("character-save").onclick = async () => {
  const saved = await api("PUT", `/api/teams/${characterTeamId}/character`, { character: $("character-select").value });
  if (saved) $("character-dialog").close();
};
function checkLine(check) {
  const pill = el("span", { className: "pill", textContent: STATUS_LABELS[check.status] || check.status });
  pill.dataset.status = check.status;
  const time = check.ms ? ` (${(check.ms / 1000).toLocaleString("de-CH", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s)` : "";
  return el("div", { className: "llm-check" }, pill, el("b", { textContent: ` ${check.name}${time}` }),
    check.detail ? el("div", { textContent: check.detail }) : "");
}

$("llm-test").onclick = async () => {
  const form = $("settings");
  const button = $("llm-test");
  const body = { azure_base_url: form.elements.azure_base_url.value.trim(), model: form.elements.model.value.trim() };
  if ($("azure-key").value.trim()) body.azure_api_key = $("azure-key").value.trim();
  button.disabled = true;
  button.textContent = "WIRD GETESTET...";
  $("llm-results").replaceChildren(el("p", { textContent: "Die Tests laufen. Das kann eine Minute dauern." }));
  const data = await api("POST", "/api/llm/test", body);
  button.disabled = false;
  button.textContent = "VERBINDUNG TESTEN";
  $("llm-results").replaceChildren(...(data ? data.checks.map(checkLine) : []));
};
$("forget-key").onclick = () => {
  if (window.confirm("Den gespeicherten Azure-API-Schlüssel löschen?")) api("PUT", "/api/settings", { azure_api_key: "" });
};
$("round-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const seconds = Math.round(Number($("round-minutes").value) * 60);
  api("POST", "/api/round/open", { seconds, stage: $("round-stage").value });
});
$("round-stage").onchange = showPickedStage;
$("show-stage").onclick = showStageDialog;
$("close-round").onclick = () => api("POST", "/api/round/close");
$("add-time").onclick = () => api("POST", "/api/round/add-time", { seconds: 60 });
function startMatch(demo = false) {
  if (!$("match-minutes").reportValidity()) return;
  const time_limit_seconds = Math.round(Number($("match-minutes").value) * 60);
  api("POST", "/api/match/start", { team_ids: [...selected], stage: $("round-stage").value, demo, time_limit_seconds });
}
$("start-match").onclick = () => startMatch();
$("demo-match").onclick = () => startMatch(true);
$("stop-match").onclick = () => api("POST", "/api/match/stop");
$("chat-qr").onclick = () => showQr("HILFE-CHAT", "/api/chat/qr", state.chat.link);
$("chat-copy").onclick = () => navigator.clipboard.writeText(state.chat.link);
$("chat-log").onclick = showChats;
$("chat-regenerate").onclick = () => {
  if (window.confirm("Den Hilfe-Chat-Link ersetzen? Der alte QR-Code funktioniert danach nicht mehr.")) api("POST", "/api/chat/regenerate");
};
$("reset").onclick = () => {
  if (window.confirm("Das ganze Event zurücksetzen? Prompts, Bots, Chats und Kämpfe werden gelöscht und Punkte auf null gesetzt.")) api("POST", "/api/reset");
};

connect();
