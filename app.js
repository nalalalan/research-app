const itemsEl = document.querySelector("#items");
const transcriptsEl = document.querySelector("#transcripts");
const transcriptInput = document.querySelector("#transcriptInput");
const analyzeButton = document.querySelector("#analyzeButton");
const intakeStatus = document.querySelector("#intakeStatus");

const saveTimers = new Map();

let items = [];
let transcripts = [];
let aiConfigured = false;
let modelName = "";
let confirmDeleteId = "";
let confirmTranscriptDeleteId = "";

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

function todoBody(item) {
  return [item.task, item.details, item.why]
    .map((part) => safeText(part).trim())
    .filter(Boolean)
    .join("\n\n");
}

function splitTodoBody(value) {
  const lines = safeText(value)
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const task = lines.shift() || "";
  return {
    task,
    details: lines.join("\n"),
    why: "",
  };
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

function makeInput(item, field, className, multiline = false) {
  const control = document.createElement(multiline ? "textarea" : "input");
  control.className = className;
  control.value = safeText(item[field]);
  control.spellcheck = true;
  control.setAttribute("aria-label", field);
  control.addEventListener("input", () => {
    item[field] = control.value;
    if (multiline) autosize(control);
    scheduleSave(item.id, { [field]: control.value });
  });
  if (multiline) requestAnimationFrame(() => autosize(control));
  return control;
}

function makeStateSelect(item) {
  const select = document.createElement("select");
  select.className = "state-select";
  select.setAttribute("aria-label", "row state");
  [
    ["review", "review"],
    ["active", "active"],
    ["done", "done"],
    ["set_aside", "set aside"],
    ["needs_evidence", "needs evidence"],
  ].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.append(option);
  });
  select.value = item.state || "review";
  select.addEventListener("change", () => {
    item.state = select.value;
    scheduleSave(item.id, { state: select.value });
  });
  return select;
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

  const top = document.createElement("div");
  top.className = "row-top";
  top.append(makeStateSelect(item));

  const source = document.createElement("span");
  source.className = "source-chip";
  const sourceParts = [item.sourceSpeaker, item.confidence].filter(Boolean);
  source.textContent = sourceParts.join(" / ") || "transcript";
  top.append(source);

  const deleteButton = document.createElement("button");
  deleteButton.className = confirmDeleteId === item.id ? "delete-row is-confirming" : "delete-row";
  deleteButton.type = "button";
  deleteButton.setAttribute("aria-label", confirmDeleteId === item.id ? "confirm delete row" : "delete row");
  deleteButton.textContent = confirmDeleteId === item.id ? "delete?" : "x";
  deleteButton.addEventListener("click", () => deleteItem(item));
  top.append(deleteButton);

  const todo = document.createElement("textarea");
  todo.className = "todo-input";
  todo.value = todoBody(item);
  todo.spellcheck = true;
  todo.setAttribute("aria-label", "todo");
  todo.addEventListener("input", () => {
    const patch = splitTodoBody(todo.value);
    item.task = patch.task;
    item.details = patch.details;
    item.why = patch.why;
    autosize(todo);
    scheduleSave(item.id, patch);
  });
  requestAnimationFrame(() => autosize(todo));
  cell.append(top, todo);

  if (item.evidence?.length) {
    const quote = document.createElement("blockquote");
    quote.textContent = item.evidence[0];
    cell.append(quote);
  }

  if (item.openQuestions?.length) {
    const questions = document.createElement("div");
    questions.className = "questions";
    questions.textContent = item.openQuestions.join(" / ");
    cell.append(questions);
  }

  return cell;
}

function buildDateTimeCell(item) {
  const cell = document.createElement("td");
  cell.className = "date-time-cell";

  const date = document.createElement("input");
  date.className = "date-input";
  date.type = "date";
  date.value = formatDate(item.dateAdded);
  date.setAttribute("aria-label", "date");
  date.addEventListener("input", () => {
    item.dateAdded = date.value;
    scheduleSave(item.id, { dateAdded: date.value });
  });

  const time = makeInput(item, "timeEstimate", "time-input");
  time.placeholder = "time";
  cell.append(date, time);
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
    buildDateTimeCell(item),
    buildScoreCell(item, "easeScore", "ease score"),
    buildScoreCell(item, "disneyScore", "disney score"),
    buildTotalCell(item),
  );
  return row;
}

function renderItems() {
  const sorted = [...items].sort((a, b) => totalScore(b) - totalScore(a));
  if (!sorted.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 5;
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

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && document.activeElement === transcriptInput) {
    event.preventDefault();
    analyzeTranscript();
  }
});

loadItems();
