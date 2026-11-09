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
      $("connection").textContent = "THIS LINK NO LONGER WORKS. ASK THE HOST FOR THE NEW QR CODE.";
      $("connection").hidden = false;
      $("chat-input").disabled = true;
      $("chat-send").disabled = true;
      return;
    }
    $("connection").textContent = "RECONNECTING...";
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
      return { mood: "wait", label: "GET READY", title: "WAITING", text: "The helper wakes up when the first round starts." };
    case "prompting":
      return { mood: "open", label: `ROUND ${s.round}`, title: "PROMPT TIME", text: "Ask away, then share your best ideas with your team." };
    case "fighting":
      return { mood: "fight", label: s.round ? `ROUND ${s.round} CLOSED` : "TEST MATCH", title: "FIGHT!", text: "Watch the big screen! You can still ask questions for the next round." };
    default:
      return { mood: "closed", label: `ROUND ${s.round} CLOSED`, title: "ROUND CLOSED", text: "Prompts are locked for now, but the helper is still here. Plan your next idea!" };
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

  const { open, busy } = s.chat;
  $("chat-input").disabled = !open;
  $("chat-send").disabled = !open || busy;
}

function scrollChat() { $("chat-log").scrollTop = $("chat-log").scrollHeight; }

function addBubble(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role}`;
  const name = document.createElement("b");
  name.textContent = role === "user" ? "YOU" : "HELPER";
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
