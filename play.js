"use strict";

const el = id => document.getElementById(id);
const arrows = {north: "↑", south: "↓", east: "→", west: "←", up: "↗", down: "↙"};
const title = text => text[0].toUpperCase() + text.slice(1);
let state, connected = false, posting = false, acknowledged = false;
let drawer = "", drawerTrigger, journalRequest = 0, oldest = null;
let imageURL = null, activeImage = null, entrySignature = "";

function remember(key, value) {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch { /* The current tab can still play when storage is unavailable. */ }
}
function recall(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}
let outbox = null;
try { outbox = JSON.parse(recall("outbox")); } catch { remember("outbox", null); }
el("command").value = recall("draft") || "";

function text(id, value) {
  if (el(id).textContent !== value) el(id).textContent = value;
}
function node(tag, value, css = "") {
  const item = document.createElement(tag);
  item.className = css;
  item.textContent = value;
  return item;
}
function commandButton(label, command, css = "") {
  const item = node("button", label, css);
  item.type = "button";
  item.dataset.command = command;
  return item;
}
function renderRoom(room) {
  text("chapter", room.chapter + " · Life " + room.life);
  el("lesson-list").replaceChildren(...room.lessons.map(lesson => node("li", lesson)));
  el("memories").hidden = drawer !== "journal" || !room.lessons.length;
  text("name", room.name);
  text("prose", room.description);
  text("count", String(room.inventory.length));
  const objects = [...room.items, ...room.scenery];
  el("object-row").hidden = !objects.length;
  el("objects").replaceChildren(...objects.map(name => commandButton(name, "examine " + name)));
  el("exit-row").hidden = !room.exits.length;
  el("exits").replaceChildren(...room.exits.map(d => commandButton(arrows[d] + " " + title(d), "go " + d)));
  el("trail-row").hidden = !room.trail.length;
  el("trail").replaceChildren(...room.trail.map(step =>
    node("li", step.way === "woke" ? "Woke at " + step.room : title(step.way) + " to " + step.room)));
  const center = node("span", "✧");
  center.setAttribute("aria-hidden", "true");
  el("sketch").replaceChildren(center, ...room.exits.map(d => commandButton(title(d), "go " + d, d)));
  const guide = node("button", "Command guide");
  guide.id = "guide";
  guide.onclick = () => openDrawer("guide", guide);
  const offers = room.offers.map(label => commandButton(label, label));
  const takes = room.items.slice(0, 3).map(n => commandButton("Take " + n, "take " + n));
  const mirror = room.inventory.length ? [commandButton("Look at yourself", "look at yourself")] : [];
  el("suggestions").replaceChildren(...offers, ...takes, ...mirror, guide);
}
function renderArt(art) {
  const portrait = art.kind === "portrait";
  const shown = "Your portrait. Your next action returns to the room.";
  const statuses = portrait
    ? {queued: "Portrait queued", generating: "Painting your portrait…", cached: shown, ready: shown, error: art.error}
    : {waiting: "Waiting for the room…", queued: "Illustration queued", generating: "Illustrating this room…",
      cached: "Cached room illustration", ready: "Room illustration", error: art.error};
  text("art-status", statuses[art.status] || "");
  el("retry").hidden = art.status !== "error";
  if (art.image === imageURL) return;
  imageURL = art.image;
  activeImage?.remove();
  activeImage = null;
  el("sketch").hidden = false;
  if (!art.image) return;
  const img = new Image();
  activeImage = img;
  img.alt = portrait ? "A portrait of your character as they look now" : "An illustration of " + state.room.name;
  img.classList.toggle("portrait", portrait);
  img.onload = () => {
    if (activeImage !== img || state.art.key !== art.key) return;
    el("sketch").after(img);
    el("sketch").hidden = true;
  };
  img.onerror = () => {
    if (activeImage === img) {
      imageURL = null; // A stale art URL can disappear between the state and image requests.
      text("art-status", "Waiting for the current illustration…");
    }
  };
  img.src = art.image;
}
function eventNodes(events, includeArrival) {
  return events.filter(event => includeArrival || event.kind !== "arrival").map(event =>
    node("p", event.text, event.kind === "chapter" ? "chapter-event" : event.kind));
}
function renderEntry(entry) {
  const signature = JSON.stringify([entry?.turn, entry?.events]);
  if (signature === entrySignature) return;
  entrySignature = signature;
  el("latest").hidden = !entry;
  if (!entry) return;
  text("last-command", entry.command);
  text("last-place", entry.place);
  el("events").replaceChildren(...eventNodes(entry.events, false));
  const latestEvent = entry.events.at(-1);
  const death = entry.events.some(event => event.kind === "death");
  if (death) text("announcement", entry.events.filter(event => event.kind !== "arrival").map(event => event.text).join(" "));
  else if (latestEvent) text("announcement", latestEvent.text);
}
function controls() {
  const busy = !connected || !state || Boolean(outbox || state.pending || state.room.ended);
  el("act").disabled = busy;
  el("command").disabled = Boolean(state?.room.ended);
  for (const btn of document.querySelectorAll("[data-command]")) btn.disabled = busy;
  if (state?.room.ended) text("activity", "This session has ended. Your journal is still available.");
  else if (!connected) text("activity", state ? "Connection lost. Reconnecting…" : "Connecting to the game…");
  else text("activity", state.pending?.activity || (outbox ? "Sending your action…" : ""));
}

function renderBag() {
  const items = state?.room.inventory || [];
  const list = node("div", "", "carried");
  const worn = new Set(state?.room.worn || []);
  list.append(...items.map(name => {
    const item = commandButton(name, "examine " + name);
    if (worn.has(name)) item.append(" ", node("em", "worn"));
    return item;
  }));
  el("drawer-body").replaceChildren(items.length ? list : node("p", "You’re carrying nothing."));
}
async function loadJournal(earlier = false) {
  const ticket = ++journalRequest;
  el("older").disabled = true;
  try {
    const url = earlier && oldest ? "/journal?before=" + oldest : "/journal";
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw Error();
    const page = await response.json();
    if (ticket !== journalRequest || drawer !== "journal") return;
    const body = el("drawer-body");
    if (!earlier) body.replaceChildren();
    if (!page.entries.length && !earlier) body.append(node("p", "Your first action will appear here."));
    for (const entry of page.entries) {
      let group = body.lastElementChild;
      if (!group || group.dataset.chapter !== entry.chapter) {
        group = document.createElement("section");
        group.dataset.chapter = entry.chapter;
        group.append(node("h3", entry.chapter), document.createElement("ol"));
        body.append(group);
      }
      const item = document.createElement("li");
      const label = node("div", "", "byline");
      label.append(node("strong", entry.command), node("span", entry.place));
      const events = node("div", "", "events");
      events.append(...eventNodes(entry.events, true));
      item.append(label, events);
      group.lastElementChild.append(item);
      oldest = entry.turn;
    }
    el("older").hidden = !page.more;
  } catch {
    if (ticket === journalRequest && drawer === "journal") text("notice", "Could not load the journal. Reopen it to retry.");
  } finally { if (ticket === journalRequest) el("older").disabled = false; }
}
function closeDrawer() {
  ++journalRequest;
  drawer = "";
  el("drawer").hidden = true;
  el("bag").setAttribute("aria-expanded", "false");
  el("history").setAttribute("aria-expanded", "false");
  const trigger = drawerTrigger?.isConnected ? drawerTrigger : el("command");
  trigger.focus({preventScroll: true});
}
function openDrawer(name, trigger) {
  if (drawer === name) { closeDrawer(); return; }
  drawer = name;
  el("memories").hidden = name !== "journal" || !state?.room.lessons.length;
  drawerTrigger = trigger;
  el("drawer").hidden = false;
  el("older").hidden = true;
  el("bag").setAttribute("aria-expanded", String(name === "bag"));
  el("history").setAttribute("aria-expanded", String(name === "journal"));
  text("drawer-title", {bag: "Inventory", journal: "Journal", guide: "Command guide"}[name]);
  if (name === "bag") renderBag();
  if (name === "journal") {
    el("drawer-body").replaceChildren(node("p", "Opening the journal…"));
    loadJournal();
  }
  if (name === "guide") el("drawer-body").replaceChildren(node("p",
    "Describe what you want to try. Click a detail to examine it or an exit to move. You can take and drop items, search a landmark, or use something you carry on something in the room. “Look at yourself” paints a portrait of what you have on. Type “help” for the full command list or “quit” to end this session."));
  controls();
  el("close").focus();
}
function forgetRequest() {
  outbox = null;
  acknowledged = false;
  remember("outbox", null);
}
async function deliver() {
  if (posting || acknowledged || !outbox || !connected) return;
  posting = true;
  const request = outbox;
  try {
    const response = await fetch("/commands", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(request)});
    const result = await response.json();
    if (response.status === 202) {
      if (outbox === request) acknowledged = true;
    } else {
      if (!el("command").value) {
        el("command").value = request.command;
        remember("draft", request.command);
      }
      forgetRequest();
      text("notice", result.error || "The turn could not be submitted.");
    }
  } catch {
    connected = false; // Reconcile the same request ID when the connection returns.
  } finally {
    posting = false;
    controls();
  }
}
function submit(command, typed = false) {
  if (el("act").disabled || !command.trim()) return;
  outbox = {id: crypto.randomUUID(), after: state.turn, command: command.trim()};
  remember("outbox", JSON.stringify(outbox));
  if (typed) {
    el("command").value = "";
    remember("draft", null);
  }
  text("notice", "");
  controls();
  deliver();
}
async function poll() {
  try {
    const response = await fetch("/state", {cache: "no-store"});
    if (!response.ok) throw Error();
    const next = await response.json();
    const changed = !state || state.turn !== next.turn;
    state = next;
    connected = true;
    if (outbox && (state.latest?.id === outbox.id || state.pending?.id === outbox.id ||
        (acknowledged && state.turn > outbox.after))) forgetRequest();
    if (changed) {
      const hadFocus = document.activeElement?.matches("[data-command], #guide");
      renderRoom(state.room);
      if (hadFocus && document.activeElement === document.body) el("command").focus({preventScroll: true});
      if (drawer === "bag") renderBag();
      if (drawer === "journal") loadJournal();
    }
    renderArt(state.art);
    renderEntry(state.pending || state.latest);
    controls();
    if (outbox) await deliver();
  } catch {
    connected = false;
    controls();
  } finally {
    setTimeout(poll, state?.pending || outbox ? 300 : 700);
  }
}

document.addEventListener("click", e => {
  const btn = e.target.closest("[data-command]");
  if (btn && !btn.disabled) submit(btn.dataset.command);
});
el("form").onsubmit = e => { e.preventDefault(); submit(el("command").value, true); };
el("command").oninput = () => remember("draft", el("command").value);
el("bag").onclick = () => openDrawer("bag", el("bag"));
el("history").onclick = () => openDrawer("journal", el("history"));
el("close").onclick = closeDrawer;
el("older").onclick = () => loadJournal(true);
document.addEventListener("keydown", e => { if (e.key === "Escape" && drawer) closeDrawer(); });
el("retry").onclick = async () => {
  el("retry").disabled = true;
  text("notice", "");
  try {
    const res = await fetch("/retry/" + state.art.key, {method: "POST"});
    if (!res.ok && res.status !== 409) throw Error();
  } catch { text("notice", "Could not request the illustration. Try again when connected."); }
  finally { el("retry").disabled = false; }
};
poll();
