"use strict";

// Shared by the team, chat and admin pages. Text from people or the AI is
// always inserted as text, never as HTML.

const $ = (id) => document.getElementById(id);

const URGENT_SECONDS = 15;

function createClock(element) {
  let end = null;
  const tick = () => {
    if (end === null) { element.hidden = true; return; }
    const seconds = Math.max(0, Math.ceil((end - performance.now()) / 1000));
    const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
    element.textContent = `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
    element.classList.toggle("urgent", seconds <= URGENT_SECONDS);
    element.hidden = false;
  };
  setInterval(tick, 250);
  return {
    set(remaining) {
      end = remaining === null ? null : performance.now() + remaining * 1000;
      tick();
    },
  };
}

// The round's stage, so the kids know what they are prompting for.
function renderStage(stage) {
  $("stage-card").hidden = !stage;
  if (!stage) return;
  $("stage-name").textContent = `BÜHNE DIESER RUNDE: ${stage.name.toUpperCase()}`;
  if ($("stage-map").getAttribute("src") !== stage.image) $("stage-map").src = stage.image;
  $("stage-map").alt = `Karte von ${stage.name}`;
  $("stage-about").textContent = stage.about;
}

function inlineMarkdown(target, text) {
  text.split(/(`[^`]+`|\*\*[^*]+\*\*)/).forEach((part) => {
    if (part.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = part.slice(1, -1);
      target.append(code);
    } else if (part.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = part.slice(2, -2);
      target.append(strong);
    } else {
      target.append(part);
    }
  });
}

function renderCheatsheet(root, markdown) {
  let list = null;
  for (const line of markdown.split("\n")) {
    if (!line.trim()) { list = null; continue; }
    if (line.startsWith("- ")) {
      if (!list) { list = document.createElement("ul"); root.append(list); }
      const item = document.createElement("li");
      inlineMarkdown(item, line.slice(2));
      list.append(item);
      continue;
    }
    list = null;
    const heading = line.match(/^(#{1,2}) (.*)/);
    const element = document.createElement(heading ? `h${heading[1].length}` : "p");
    inlineMarkdown(element, heading ? heading[2].toUpperCase() : line);
    root.append(element);
  }
}

function loadCheatsheet(root) {
  fetch("/cheatsheet.md").then((response) => response.text()).then((text) => renderCheatsheet(root, text));
}
