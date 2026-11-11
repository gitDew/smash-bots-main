"use strict";

// The page for the one kid on each team who writes the prompt.

const token = location.pathname.split("/").filter(Boolean).pop();
const SAVE_DELAY_MS = 250;
const LINK_GONE_CODES = [4403, 4404];

let socket = null;
let retryMs = 1000;
let state = null;
let dirty = false;
let promptSeq = 0;
let saveTimer = null;
let shownCodeId = null;
let shownRound = null;
let shownActivityRound = null;
const clock = createClock($("status-clock"));

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
    return true;
  }
  return false;
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws/${token}`);
  socket.onopen = () => {
    retryMs = 1000;
    $("connection").hidden = true;
    flushPrompt();
  };
  socket.onmessage = (event) => handle(JSON.parse(event.data));
  socket.onclose = (event) => {
    if (LINK_GONE_CODES.includes(event.code)) {
      $("connection").textContent = "DIESER LINK FUNKTIONIERT NICHT MEHR. FRAG DIE SPIELLEITUNG NACH EINEM NEUEN.";
      $("connection").hidden = false;
      $("prompt").readOnly = true;
      return;
    }
    $("connection").textContent = "VERBINDUNG WIRD WIEDERHERGESTELLT...";
    $("connection").hidden = false;
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 1.6, 5000);
  };
}

function handle(message) {
  switch (message.type) {
    case "state": renderState(message); break;
    case "prompt":
      if (!dirty) { $("prompt").value = message.text; updateCount(); }
      break;
    case "prompt_saved":
      if (message.seq === promptSeq) { dirty = false; setSaveState("GESPEICHERT"); }
      break;
    case "prompt_rejected":
      dirty = false;
      setSaveState("ZU SPÄT: DIE RUNDE WAR SCHON GESCHLOSSEN");
      break;
    case "agent_activity_history":
      $("activity-list").replaceChildren();
      shownActivityRound = null;
      message.entries.forEach(addActivity);
      break;
    case "agent_activity": addActivity(message.entry); break;
  }
}

function statusText(s) {
  const running = s.agent && ["queued", "running"].includes(s.agent.status);
  switch (s.phase) {
    case "setup":
      return { mood: "wait", label: "MACH DICH BEREIT", title: "WARTEN", text: "Die erste Runde startet bald." };
    case "prompting":
      return { mood: "open", label: `RUNDE ${s.round}`, title: "PROMPT-ZEIT", text: "Alles, was du schreibst, wird automatisch gespeichert. Wenn die Zeit abläuft, beginnt die KI zu programmieren." };
    case "coding":
      return { mood: "closed", label: `RUNDE ${s.round} GESCHLOSSEN`, title: running ? "KI PROGRAMMIERT" : "BOT FERTIG", text: "Der Prompt für diese Runde kann nicht mehr geändert werden." };
    case "ready":
      return { mood: "closed", label: `RUNDE ${s.round} GESCHLOSSEN`, title: "BOTS BEREIT", text: "Der Prompt für diese Runde kann nicht mehr geändert werden. Lies deinen Code und überlege dir deine nächste Idee." };
    case "fighting":
      if (s.demo) return { mood: "fight", label: "DEMO", title: "SCHAU ZU!", text: "Niemand hat den Bots bisher gesagt, was sie tun sollen. Deshalb stehen sie nur da. Dein Prompt ändert das!" };
      return { mood: "fight", label: s.round ? `RUNDE ${s.round} GESCHLOSSEN` : "TESTKAMPF", title: "KAMPF!", text: "Schau auf den grossen Bildschirm!" };
    default:
      return { mood: "wait", label: "", title: "", text: "" };
  }
}

function renderState(s) {
  const roundChanged = shownRound !== null && shownRound !== s.round;
  // The prompt is locked now, so bring the kids to the live progress.
  if (state && state.phase === "prompting" && s.phase === "coding") document.body.dataset.tab = "bot";
  shownRound = s.round;
  state = s;
  $("team-name").textContent = s.team.name;
  $("character-line").textContent = `Du kämpfst als ${s.team.character}. Die Spielleitung wählt die Figuren aus. Das kannst du nicht ändern.`;
  $("character-line").hidden = !s.team.character;

  const status = statusText(s);
  $("status").dataset.mood = status.mood;
  $("status-label").textContent = status.label;
  $("status-title").textContent = status.title;
  $("status-text").textContent = status.text;
  $("lock-icon").hidden = status.mood !== "closed";
  clock.set(s.prompt_open ? s.remaining : null);

  const prompt = $("prompt");
  prompt.maxLength = s.max_prompt_chars;
  prompt.readOnly = !s.prompt_open;
  $("locked-stamp").hidden = s.prompt_open || !s.round;
  if (roundChanged) dirty = false;
  if (!dirty) prompt.value = s.prompt;
  if (s.prompt_open) {
    if (dirty) flushPrompt(); else setSaveState(s.prompt ? "GESPEICHERT" : "");
  } else {
    dirty = false;
    setSaveState(s.round ? "RUNDE GESCHLOSSEN" : "NOCH NICHT GEÖFFNET");
  }
  updateCount();
  renderStage(s.stage);
  $("team-points").textContent = `score:${s.team.points}`;
  renderStartChoice(s);
  renderAgent(s);
  renderCode(s);
}

function renderStartChoice(s) {
  const dialog = $("start-dialog");
  const { asked, mode } = s.start;
  if (asked && mode === null && !dialog.open) dialog.showModal();
  if (!asked && dialog.open) dialog.close();
  $("mode-line").hidden = !asked || mode === null;
  $("mode-text").textContent = mode === "fresh"
    ? "NEU ANFANGEN: Dein alter Code wird am Ende dieser Runde gelöscht."
    : "WEITER VERBESSERN: Die KI verbessert deinen bisherigen Bot.";
}

function chooseStart(mode) {
  if (send({ type: "start_mode", mode })) $("start-dialog").close();
}

$("choose-keep").addEventListener("click", () => chooseStart("keep"));
$("choose-fresh").addEventListener("click", () => chooseStart("fresh"));
$("mode-change").addEventListener("click", () => $("start-dialog").showModal());
// The question needs an answer, so Escape must not dismiss it while unanswered.
$("start-dialog").addEventListener("cancel", (event) => {
  if (state && state.start.mode === null) event.preventDefault();
});

function renderAgent(s) {
  const agent = s.agent;
  const card = $("agent-card");
  card.dataset.status = agent ? agent.status : "none";
  if (!agent) {
    $("agent-title").textContent = "BEREIT";
    $("agent-text").textContent = "Dein Start-Bot wartet auf deine erste Idee.";
    $("agent-text").classList.remove("working-dots");
    return;
  }
  const working = ["queued", "running"].includes(agent.status);
  $("agent-title").textContent = working ? `RUNDE ${agent.round}: IN ARBEIT` : agent.status === "failed" ? `RUNDE ${agent.round}: UPS` : `RUNDE ${agent.round}: FERTIG`;
  $("agent-text").textContent = working ? agent.step || "In Arbeit" : agent.error || agent.summary || "";
  $("agent-text").classList.toggle("working-dots", working);
  if (working) $("activity-box").open = true;
}

function addActivity(entry) {
  const list = $("activity-list");
  if (entry.round !== shownActivityRound) {
    list.replaceChildren();
    shownActivityRound = entry.round;
  }
  const line = document.createElement("li");
  line.dataset.tone = entry.tone;
  line.textContent = entry.label;
  list.append(line);
  list.scrollTop = list.scrollHeight;
  $("activity-box").hidden = false;
}

const TOKEN_PATTERN = /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|\b(def|class|return|if|elif|else|for|while|in|not|and|or|is|None|True|False|import|from|as|try|except|finally|with|pass|break|continue|lambda|self)\b|\b(\d+(?:\.\d+)?)\b/g;
const TOKEN_CLASSES = ["tok-comment", "tok-string", "tok-keyword", "tok-number"];

function highlight(target, code) {
  target.replaceChildren();
  let end = 0;
  for (const match of code.matchAll(TOKEN_PATTERN)) {
    target.append(code.slice(end, match.index));
    const span = document.createElement("span");
    span.className = TOKEN_CLASSES[match.slice(1).findIndex((group) => group !== undefined)];
    span.textContent = match[0];
    target.append(span);
    end = match.index + match[0].length;
  }
  target.append(code.slice(end));
}

function renderCode(s) {
  $("code-area").hidden = !s.code;
  $("code-wait").hidden = Boolean(s.code);
  if (!s.code || s.code.id === shownCodeId) return;
  const isNews = shownCodeId !== null && document.body.dataset.tab !== "bot";
  shownCodeId = s.code.id;
  const lines = s.code.text.replace(/\n$/, "").split("\n");
  $("code-lines").textContent = lines.map((_, index) => index + 1).join("\n");
  highlight($("code-text"), s.code.text);
  $("code-title").textContent = s.code.round ? `DER CODE NACH RUNDE ${s.code.round}` : "DEIN START-CODE";
  document.querySelector('.tab[data-tab="bot"]').classList.toggle("has-news", isNews);
}

function setSaveState(text) { $("save-state").textContent = text; }

function updateCount() {
  const max = state ? state.max_prompt_chars : 0;
  $("char-count").textContent = `${$("prompt").value.length}/${max}`;
}

function flushPrompt() {
  clearTimeout(saveTimer);
  if (!dirty || !state || !state.prompt_open) return;
  promptSeq += 1;
  if (!send({ type: "prompt", text: $("prompt").value, seq: promptSeq })) setSaveState("OFFLINE: WIRD BEI VERBINDUNG GESPEICHERT");
}

$("prompt").addEventListener("input", () => {
  dirty = true;
  setSaveState("WIRD GESPEICHERT...");
  updateCount();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushPrompt, SAVE_DELAY_MS);
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.body.dataset.tab = tab.dataset.tab;
    tab.classList.remove("has-news");
  });
});

loadCheatsheet($("cheatsheet"));
connect();
