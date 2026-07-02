const itemsEl = document.querySelector("#items");
const transcriptsEl = document.querySelector("#transcripts");
const transcriptInput = document.querySelector("#transcriptInput");
const analyzeButton = document.querySelector("#analyzeButton");
const intakeStatus = document.querySelector("#intakeStatus");
const sortButtons = [...document.querySelectorAll(".sort-button")];

const saveTimers = new Map();

let items = [];
let transcripts = [];
let aiConfigured = false;
let modelName = "";
let confirmDeleteId = "";
let confirmTranscriptDeleteId = "";
let sortState = { key: "", direction: "desc" };

function setStatus(text, tone = "") {
  intakeStatus.textContent = text;
  intakeStatus.dataset.tone = tone;
}

function safeText(value) {
  return value == null ? "" : String(value);
}

function scoreValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number)));
}

function totalScore(item) {
  return scoreValue(item.easeScore) + scoreValue(item.disneyScore);
}

function formatDate(value) {
  const text = safeText(value);
  if (!text) return "";
  return text.slice(0, 10);
}

function compactParts(parts) {
  return parts
    .map((part) => safeText(part).trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function fixBody(item) {
  return compactParts([item.task, item.details, item.why]);
}

function splitFixBody(value) {
  return {
    task: safeText(value).replace(/\s+/g, " ").trim(),
    details: "",
    why: "",
  };
}

function fixMeta(item) {
  return [formatDate(item.dateAdded), safeText(item.timeEstimate).trim(), safeText(item.sourceSpeaker).trim()]
    .filter(Boolean)
    .join(" / ");
}

function scheduleSave(id, patch) {
  const existing = saveTimers.get(id);
  if (existing) clearTimeout(existing);
  saveTimers.set(
    id,
    setTimeout(async () => {
      saveTimers.delete(id);
      const response = await fetch(`/api/todo/items/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      setStatus(response.ok ? "saved" : "save failed", response.ok ? "" : "bad");
    }, 450),
  );
}

function autosize(textarea) {
  textarea.style.height = "0px";
  textarea.style.height = `${Math.min(260, Math.max(34, textarea.scrollHeight))}px`;
}

async function deleteItem(item) {
  if (confirmDeleteId !== item.id) {
    confirmDeleteId = item.id;
    renderItems();
    return;
  }
  const response = await fetch(`/api/todo/items/${item.id}`, { method: "DELETE" });
  if (!response.ok) {
    setStatus("delete failed", "bad");
    return;
  }
  items = items.filter((entry) => entry.id !== item.id);
  confirmDeleteId = "";
  renderItems();
  setStatus("deleted");
}

async function markDone(item) {
  if (item.state === "done") {
    setStatus("done");
    return;
  }
  const response = await fetch(`/api/todo/items/${item.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state: "done" }),
  });
  if (!response.ok) {
    setStatus("save failed", "bad");
    return;
  }
  item.state = "done";
  renderItems();
  setStatus("done");
}

async function deleteTranscript(entry) {
  if (confirmTranscriptDeleteId !== entry.id) {
    confirmTranscriptDeleteId = entry.id;
    renderTranscripts();
    return;
  }
  const response = await fetch(`/api/todo/transcripts/${entry.id}`, { method: "DELETE" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    setStatus(payload.detail || "delete failed", "bad");
    return;
  }
  items = payload.items || items.filter((item) => item.sourceTranscriptId !== entry.id);
  transcripts = payload.transcripts || transcripts.filter((transcript) => transcript.id !== entry.id);
  confirmTranscriptDeleteId = "";
  render();
  const removed = payload.removedItems || 0;
  setStatus(removed ? `deleted transcription and ${removed} rows` : "deleted transcription");
}

function buildTodoCell(item) {
  const cell = document.createElement("td");
  cell.className = "todo-cell";

  const actions = document.createElement("div");
  actions.className = "row-actions";

  const doneButton = document.createElement("button");
  doneButton.className = item.state === "done" ? "done-row is-done" : "done-row";
  doneButton.type = "button";
  doneButton.setAttribute("aria-label", "mark row done");
  doneButton.textContent = "done";
  doneButton.addEventListener("click", () => markDone(item));
  actions.append(doneButton);

  const deleteButton = document.createElement("button");
  deleteButton.className = confirmDeleteId === item.id ? "delete-row is-confirming" : "delete-row";
  deleteButton.type = "button";
  deleteButton.setAttribute("aria-label", confirmDeleteId === item.id ? "confirm delete row" : "delete row");
  deleteButton.textContent = confirmDeleteId === item.id ? "delete?" : "delete";
  deleteButton.addEventListener("click", () => deleteItem(item));
  actions.append(deleteButton);

  const sections = document.createElement("div");
  sections.className = "todo-sections";

  const fixSection = document.createElement("section");
  fixSection.className = "todo-section";

  const fixLabel = document.createElement("div");
  fixLabel.className = "todo-section-label";
  fixLabel.textContent = "fix";

  const meta = document.createElement("div");
  meta.className = "todo-meta";
  meta.textContent = fixMeta(item);

  const todo = document.createElement("textarea");
  todo.className = "fix-input";
  todo.value = fixBody(item);
  todo.spellcheck = true;
  todo.setAttribute("aria-label", "fix");
  todo.addEventListener("input", () => {
    const patch = splitFixBody(todo.value);
    item.task = patch.task;
    item.details = patch.details;
    item.why = patch.why;
    autosize(todo);
    scheduleSave(item.id, patch);
  });
  requestAnimationFrame(() => autosize(todo));
  fixSection.append(fixLabel);
  if (meta.textContent) fixSection.append(meta);
  fixSection.append(todo);

  const quoteSection = document.createElement("section");
  quoteSection.className = "todo-section";

  const quoteLabel = document.createElement("div");
  quoteLabel.className = "todo-section-label";
  quoteLabel.textContent = "quote";

  const quote = document.createElement("blockquote");
  quote.className = "quote-block";
  quote.textContent = item.evidence?.length ? item.evidence[0] : "no quote saved";

  quoteSection.append(quoteLabel, quote);
  sections.append(fixSection, quoteSection);
  cell.append(actions, sections);

  return cell;
}

function buildScoreCell(item, field, label) {
  const cell = document.createElement("td");
  cell.className = "score-cell";

  const input = document.createElement("input");
  input.className = "score-input";
  input.type = "number";
  input.min = "0";
  input.max = "100";
  input.value = scoreValue(item[field]);
  input.setAttribute("aria-label", label);

  const bar = document.createElement("span");
  bar.className = "score-bar";
  bar.style.setProperty("--score", `${scoreValue(item[field])}%`);

  input.addEventListener("input", () => {
    const score = scoreValue(input.value);
    item[field] = score;
    bar.style.setProperty("--score", `${score}%`);
    rowTotalRefresh(item.id);
    scheduleSave(item.id, { [field]: score });
  });

  cell.append(input, bar);
  return cell;
}

function rowTotalRefresh(id) {
  const item = items.find((entry) => entry.id === id);
  const row = document.querySelector(`tr[data-id="${CSS.escape(id)}"]`);
  if (!item || !row) return;
  const total = row.querySelector(".total-score");
  const totalBar = row.querySelector(".total-bar");
  if (!total || !totalBar) return;
  const value = totalScore(item);
  total.textContent = String(value);
  totalBar.style.setProperty("--score", `${Math.min(100, Math.round(value / 2))}%`);
}

function buildTotalCell(item) {
  const cell = document.createElement("td");
  cell.className = "total-cell";
  const value = totalScore(item);
  const number = document.createElement("span");
  number.className = "total-score";
  number.textContent = String(value);
  const bar = document.createElement("span");
  bar.className = "total-bar";
  bar.style.setProperty("--score", `${Math.min(100, Math.round(value / 2))}%`);
  cell.append(number, bar);
  return cell;
}

function buildRow(item) {
  const row = document.createElement("tr");
  row.className = `todo-row state-${item.state || "review"}`;
  row.dataset.id = item.id;
  row.append(
    buildTodoCell(item),
    buildScoreCell(item, "easeScore", "ease score"),
    buildScoreCell(item, "disneyScore", "disney score"),
    buildTotalCell(item),
  );
  return row;
}

function sortValue(item, key) {
  if (key === "todo") return fixBody(item).toLowerCase();
  if (key === "ease") return scoreValue(item.easeScore);
  if (key === "disney") return scoreValue(item.disneyScore);
  return totalScore(item);
}

function sortedItems() {
  const activeKey = sortState.key || "total";
  const direction = sortState.key ? sortState.direction : "desc";
  return [...items].sort((a, b) => {
    const left = sortValue(a, activeKey);
    const right = sortValue(b, activeKey);
    let result = 0;
    if (typeof left === "string" || typeof right === "string") {
      result = String(left).localeCompare(String(right));
    } else {
      result = left - right;
    }
    return direction === "asc" ? result : -result;
  });
}

function updateSortHeaders() {
  sortButtons.forEach((button) => {
    const active = button.dataset.sortKey === sortState.key;
    const th = button.closest("th");
    button.dataset.active = active ? "true" : "false";
    button.dataset.dir = active ? sortState.direction : "";
    button.setAttribute("aria-pressed", active ? "true" : "false");
    if (th) th.setAttribute("aria-sort", active ? (sortState.direction === "asc" ? "ascending" : "descending") : "none");
  });
}

function renderItems() {
  updateSortHeaders();
  const sorted = sortedItems();
  if (!sorted.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "no todo rows yet";
    row.append(cell);
    itemsEl.replaceChildren(row);
    return;
  }
  itemsEl.replaceChildren(...sorted.map(buildRow));
}

function renderTranscripts() {
  if (!transcripts.length) {
    const empty = document.createElement("div");
    empty.className = "empty-transcripts";
    empty.textContent = "no transcriptions yet";
    transcriptsEl.replaceChildren(empty);
    return;
  }
  const cards = [...transcripts].reverse().map((entry) => {
    const card = document.createElement("article");
    card.className = "transcript-card";

    const title = document.createElement("div");
    title.className = "transcript-title";
    title.textContent = entry.name || "transcription";

    const meta = document.createElement("div");
    meta.className = "transcript-meta";
    const parts = [
      entry.meetingDateTime || "date not stated",
      `${entry.itemCount || 0} rows`,
      `${(entry.characterCount || 0).toLocaleString()} chars`,
    ];
    meta.textContent = parts.join(" / ");

    const basis = document.createElement("div");
    basis.className = "transcript-basis";
    basis.textContent = entry.metadataBasis || "";

    const link = document.createElement("a");
    link.className = "pdf-link";
    link.href = entry.pdfUrl || `/api/todo/transcripts/${entry.id}/pdf`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "open pdf";

    const deleteButton = document.createElement("button");
    deleteButton.className =
      confirmTranscriptDeleteId === entry.id ? "transcript-delete is-confirming" : "transcript-delete";
    deleteButton.type = "button";
    deleteButton.setAttribute(
      "aria-label",
      confirmTranscriptDeleteId === entry.id ? "confirm delete transcription" : "delete transcription",
    );
    deleteButton.textContent = confirmTranscriptDeleteId === entry.id ? "delete?" : "delete";
    deleteButton.addEventListener("click", () => deleteTranscript(entry));

    const actions = document.createElement("div");
    actions.className = "transcript-actions";
    actions.append(link, deleteButton);

    card.append(title, meta);
    if (basis.textContent) card.append(basis);
    card.append(actions);
    return card;
  });
  transcriptsEl.replaceChildren(...cards);
}

function render() {
  renderItems();
  renderTranscripts();
}

async function loadItems() {
  const response = await fetch("/api/todo/items");
  if (!response.ok) {
    setStatus("load failed", "bad");
    return;
  }
  const payload = await response.json();
  items = payload.items || [];
  transcripts = payload.transcripts || [];
  aiConfigured = Boolean(payload.aiConfigured);
  modelName = payload.model || "";
  render();
  setStatus(aiConfigured ? `ready / ${modelName}` : "AI key missing", aiConfigured ? "" : "bad");
}

async function analyzeTranscript() {
  const transcript = transcriptInput.value.trim();
  if (!transcript) {
    transcriptInput.focus();
    setStatus("paste a transcription", "bad");
    return;
  }
  analyzeButton.disabled = true;
  setStatus("analyzing");
  try {
    const response = await fetch("/api/todo/transcripts/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setStatus(payload.detail || "analysis failed", "bad");
      return;
    }
    items = payload.allItems || items;
    transcripts = payload.allTranscripts || transcripts;
    render();
    transcriptInput.value = "";
    const count = payload.items?.length || 0;
    setStatus(count ? `added ${count} rows` : "saved transcription, no supported todos found");
  } finally {
    analyzeButton.disabled = false;
  }
}

analyzeButton.addEventListener("click", analyzeTranscript);

sortButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const key = button.dataset.sortKey || "total";
    if (sortState.key === key) {
      sortState.direction = sortState.direction === "desc" ? "asc" : "desc";
    } else {
      sortState = { key, direction: key === "todo" ? "asc" : "desc" };
    }
    renderItems();
  });
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && document.activeElement === transcriptInput) {
    event.preventDefault();
    analyzeTranscript();
  }
});

loadItems();
