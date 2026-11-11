"use strict";

// The helper page: anyone with the event's QR code, each on their own phone.

const token = location.pathname.split("/").filter(Boolean).pop();
const SESSION_KEY = `smash-chat-${token}`;
const LINK_GONE_CODES = [4403, 4404];

let socket = null;
let retryMs = 1000;
let streamBubble = null;
const clock = createClock($("status-clock"));

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const session = encodeURIComponent(localStorage.getItem(SESSION_KEY) || "");
  socket = new WebSocket(`${scheme}://${location.host}/ws-chat/${token}?session=${session}`);
  socket.onopen = () => {
    retryMs = 1000;
    $("connection").hidden = true;
  };
  socket.onmessage = (event) => handle(JSON.parse(event.data));
  socket.onclose = (event) => {
    if (LINK_GONE_CODES.includes(event.code)) {
      $("connection").textContent = "DIESER LINK FUNKTIONIERT NICHT MEHR. FRAG DIE SPIELLEITUNG NACH DEM NEUEN QR-CODE.";
      $("connection").hidden = false;
      $("chat-input").disabled = true;
      $("chat-send").disabled = true;
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
    case "session": localStorage.setItem(SESSION_KEY, message.id); break;
    case "state": renderState(message); break;
    case "chat_history":
      $("chat-log").replaceChildren();
      streamBubble = null;
      message.messages.forEach((m) => addBubble(m.role, m.content));
      break;
    case "chat_message": addBubble("user", message.message.content); break;
    case "chat_start": streamBubble = addBubble("assistant", ""); break;
    case "chat_delta":
      if (!streamBubble) streamBubble = addBubble("assistant", "");
      streamBubble.append(message.text);
      scrollChat();
      break;
    case "chat_done":
      if (!streamBubble) addBubble("assistant", message.message.content);
      streamBubble = null;
      break;
    case "chat_error":
    case "chat_rejected":
      streamBubble = null;
      $("chat-foot").textContent = message.message;
      break;
  }
}

function statusText(s) {
  switch (s.phase) {
    case "setup":
      return { mood: "wait", label: "MACH DICH BEREIT", title: "WARTEN", text: "Der Hilfe-Chat ist da, sobald die erste Runde startet." };
    case "prompting":
      return { mood: "open", label: `RUNDE ${s.round}`, title: "PROMPT-ZEIT", text: "Frag drauflos und teile deine besten Ideen mit deinem Team." };
    case "fighting":
      if (s.demo) return { mood: "fight", label: "DEMO", title: "SCHAU ZU!", text: "Niemand hat den Bots bisher gesagt, was sie tun sollen. Deshalb stehen sie nur da. Was soll deiner tun?" };
      return { mood: "fight", label: s.round ? `RUNDE ${s.round} GESCHLOSSEN` : "TESTKAMPF", title: "KAMPF!", text: "Schau auf den grossen Bildschirm! Du kannst weiterhin Fragen für die nächste Runde stellen." };
    default: {
      const last = s.scores.last;
      if (s.phase === "ready" && last && last.round === s.round) {
        const title = last.winner ? `${last.winner.toUpperCase()} HAT GEWONNEN!` : "NIEMAND HAT GEWONNEN";
        return { mood: "fight", label: `KAMPF IN RUNDE ${s.round}`, title, text: "Der Hilfe-Chat ist weiterhin da. Überlege dir deine nächste Idee!" };
      }
      return { mood: "closed", label: `RUNDE ${s.round} GESCHLOSSEN`, title: "RUNDE GESCHLOSSEN", text: "Die Prompts sind gerade gesperrt, aber der Hilfe-Chat ist weiterhin da. Überlege dir deine nächste Idee!" };
    }
  }
}

function renderState(s) {
  const status = statusText(s);
  $("status").dataset.mood = status.mood;
  $("status-label").textContent = status.label;
  $("status-title").textContent = status.title;
  $("status-text").textContent = status.text;
  $("lock-icon").hidden = status.mood !== "closed";
  clock.set(s.prompt_open ? s.remaining : null);
  renderStage(s.stage);
  renderScores(s.scores);

  const { open, busy } = s.chat;
  $("chat-input").disabled = !open;
  $("chat-send").disabled = !open || busy;
}

function scrollChat() { $("chat-log").scrollTop = $("chat-log").scrollHeight; }

function addBubble(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role}`;
  const name = document.createElement("b");
  name.textContent = role === "user" ? "DU" : "HILFE-KI";
  const body = document.createElement("span");
  body.textContent = text;
  bubble.append(name, body);
  $("chat-log").append(bubble);
  scrollChat();
  return body;
}

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (text && socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "chat", text }));
    $("chat-input").value = "";
    $("chat-foot").textContent = "";
  }
});

loadCheatsheet($("cheatsheet"));
connect();
