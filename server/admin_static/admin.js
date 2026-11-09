"use strict";

// Team names, prompts and chats are written by kids or the AI: always insert as text.

const PHASE_TITLES = {
  setup: "SETUP", prompting: "PROMPTS OPEN", coding: "AGENTS CODING", ready: "BOTS READY", fighting: "MATCH RUNNING",
};
const MOODS = { setup: "wait", prompting: "open", coding: "closed", ready: "closed", fighting: "fight" };

let state = null;
let settingsFilled = false;
const clock = createClock($("status-clock"));
const selected = new Set();
const feeds = new Map();
let characterTeamId = null;

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function api(method, path, body) {
  $("error").textContent = "";
  try {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-Smash-Admin": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = typeof data.detail === "string" ? data.detail : "That was not accepted. Check the values.";
      $("error").textContent = detail;
      window.scrollTo({ top: 0 });
      return null;
    }
    return await response.json();
  } catch {
    $("error").textContent = "Could not reach the server.";
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
    el("span", { className: "feed-time", textContent: new Date(entry.time * 1000).toLocaleTimeString() }),
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

function teamRow(team, phase) {
  const agent = team.agent;
  const agentCell = el("td");
  if (agent) {
    agentCell.append(el("span", { className: "pill", textContent: agent.status.toUpperCase() }));
    agentCell.lastChild.dataset.status = agent.status;
    const text = agent.step || agent.error || "";
    if (text) agentCell.append(el("div", { textContent: text }));
    if (agent.detail) agentCell.append(el("div", { className: "warn", textContent: agent.detail }));
  }
  const pick = el("input", { type: "checkbox", checked: selected.has(team.id), ariaLabel: `Include ${team.name} in the match` });
  pick.onchange = () => { if (pick.checked) selected.add(team.id); else selected.delete(team.id); updateButtons(); };

  const actions = el("td", { className: "row" });
  actions.append(
    button("QR", "", () => showQr(team.name, `/api/teams/${team.id}/qr`, team.link)),
    button("COPY LINK", "", () => navigator.clipboard.writeText(team.link)),
    button("DETAILS", "", () => showDetail(team.id)),
    button("RENAME", "", () => {
      const name = window.prompt("New team name", team.name);
      if (name) api("PUT", `/api/teams/${team.id}`, { name });
    }),
    button("RETRY AGENT", "btn-warn", () => api("POST", `/api/teams/${team.id}/retry`)),
    button("NEW LINK", "btn-warn", () => {
      if (window.confirm(`Replace ${team.name}'s link? The old one stops working.`)) api("POST", `/api/teams/${team.id}/regenerate`);
    }),
    button("DELETE", "btn-stop", () => {
      if (window.confirm(`Delete ${team.name}, its container and its bot history?`)) api("DELETE", `/api/teams/${team.id}`);
    }),
  );
  actions.querySelectorAll("button")[4].disabled = phase !== "ready";
  return el("tr", {},
    el("td", {}, pick),
    el("td", { textContent: team.name }),
    el("td", {}, button(
      team.character ? state.characters[team.character] : "CHOOSE",
      team.character ? "" : "btn-warn",
      () => showCharacterDialog(team),
    )),
    el("td", { textContent: String(team.online) }),
    el("td", {},
      el("div", { className: "prompt-preview", textContent: team.prompt }),
      team.start_mode === "fresh" ? el("span", { className: "pill", textContent: "CLEAN START" }) : "",
    ),
    agentCell,
    actions,
  );
}

function updateButtons() {
  if (!state) return;
  const { phase } = state;
  $("open-round").disabled = !["setup", "ready"].includes(phase);
  $("close-round").disabled = phase !== "prompting";
  $("add-time").disabled = phase !== "prompting";
  $("start-match").disabled = !["setup", "ready"].includes(phase) || selected.size < 2 || selected.size > 4;
  $("stop-match").disabled = phase !== "fighting";
  $("reset").disabled = ["coding", "fighting"].includes(phase);
}

function render(s) {
  state = s;
  $("status").dataset.mood = MOODS[s.phase];
  $("status-label").textContent = s.round ? `ROUND ${s.round}` : "BEFORE ROUND 1";
  $("status-title").textContent = PHASE_TITLES[s.phase];
  clock.set(s.remaining);

  const problems = [];
  if (!s.health.llm) problems.push("AZURE: ENTER THE BASE URL AND API KEY IN THE SETTINGS");
  $("azure-key").placeholder = s.health.key_saved ? "Saved. Type a new key to replace it" : "Paste your key";
  if (s.health.docker !== "ok") problems.push("SANDBOX IMAGE IS MISSING");
  $("health").textContent = problems.join(" / ");
  $("health").hidden = problems.length === 0;

  const known = new Set(s.teams.map((team) => team.id));
  [...selected].forEach((id) => { if (!known.has(id)) selected.delete(id); });
  $("teams").replaceChildren(...s.teams.map((team) => teamRow(team, s.phase)));
  fillSelect($("team-character-input"), [["", "Choose character"], ...freeCharacters()]);
  syncFeeds(s.teams);

  $("match-log").textContent = s.match_log.length ? `${s.match_log.join("\n")}\n` : "";
  $("match-log").scrollTop = $("match-log").scrollHeight;
  $("matches").replaceChildren(...s.matches.map((match) => el("p", {
    textContent: `Round ${match.round}: ${match.teams.join(" vs ")}. ${match.status}${match.winner ? `, winner ${match.winner}` : ""}`,
  })));

  $("chat-stats").textContent = `${s.chat.phones} phones connected. ${s.chat.questions} questions answered this round.`;
  if (!settingsFilled) fillSettings(s.settings);
  updateButtons();
}

function fillSettings(settings) {
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
  $("detail-title").textContent = "HELPER CHATS";
  $("detail-body").replaceChildren(...data.messages.map((message) => el("p", {
    textContent: `[${message.session.slice(0, 4)}] Round ${message.round} ${message.role === "user" ? "Kid" : "Helper"}: ${message.content}`,
  })));
  $("detail-dialog").showModal();
}

async function showDetail(teamId) {
  const detail = await api("GET", `/api/teams/${teamId}/detail`);
  if (!detail) return;
  $("detail-title").textContent = detail.name;
  const body = $("detail-body");
  body.replaceChildren(el("h3", { textContent: "PROMPTS" }));
  detail.prompts.forEach((prompt) => body.append(el("p", { textContent: `Round ${prompt.round}: ${prompt.text}` })));
  body.append(el("h3", { textContent: "BOT VERSIONS" }));
  detail.versions.forEach((version) => {
    const roll = button("ROLL BACK TO THIS", "btn-warn", async () => {
      if (window.confirm("Make this the team's current bot?")) {
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
    $("error").textContent = "A new team needs a name and a character.";
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
    $("settings-saved").textContent = "Saved";
    setTimeout(() => { $("settings-saved").textContent = ""; }, 2000);
  }
});

$("character-save").onclick = async () => {
  const saved = await api("PUT", `/api/teams/${characterTeamId}/character`, { character: $("character-select").value });
  if (saved) $("character-dialog").close();
};
function checkLine(check) {
  const pill = el("span", { className: "pill", textContent: check.status.toUpperCase() });
  pill.dataset.status = check.status;
  const time = check.ms ? ` (${(check.ms / 1000).toFixed(1)}s)` : "";
  return el("div", { className: "llm-check" }, pill, el("b", { textContent: ` ${check.name}${time}` }),
    check.detail ? el("div", { textContent: check.detail }) : "");
}

$("llm-test").onclick = async () => {
  const form = $("settings");
  const button = $("llm-test");
  const body = { azure_base_url: form.elements.azure_base_url.value.trim(), model: form.elements.model.value.trim() };
  if ($("azure-key").value.trim()) body.azure_api_key = $("azure-key").value.trim();
  button.disabled = true;
  button.textContent = "TESTING...";
  $("llm-results").replaceChildren(el("p", { textContent: "Running the checks. This can take a minute." }));
  const data = await api("POST", "/api/llm/test", body);
  button.disabled = false;
  button.textContent = "TEST CONNECTION";
  $("llm-results").replaceChildren(...(data ? data.checks.map(checkLine) : []));
};
$("forget-key").onclick = () => {
  if (window.confirm("Remove the saved Azure API key?")) api("PUT", "/api/settings", { azure_api_key: "" });
};
$("open-round").onclick = () => api("POST", "/api/round/open");
$("close-round").onclick = () => api("POST", "/api/round/close");
$("add-time").onclick = () => api("POST", "/api/round/add-time", { seconds: 60 });
$("start-match").onclick = () => api("POST", "/api/match/start", { team_ids: [...selected] });
$("stop-match").onclick = () => api("POST", "/api/match/stop");
$("chat-qr").onclick = () => showQr("HELPER CHAT", "/api/chat/qr", state.chat.link);
$("chat-copy").onclick = () => navigator.clipboard.writeText(state.chat.link);
$("chat-log").onclick = showChats;
$("chat-regenerate").onclick = () => {
  if (window.confirm("Replace the helper chat link? The old QR code stops working.")) api("POST", "/api/chat/regenerate");
};
$("reset").onclick = () => {
  if (window.confirm("Reset the whole event? Prompts, bots, chats and matches will be deleted.")) api("POST", "/api/reset");
};

connect();
