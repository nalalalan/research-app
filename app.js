const itemsEl = document.querySelector("#items");
const transcriptInput = document.querySelector("#transcriptInput");
const transcriptName = document.querySelector("#transcriptName");
const transcriptFile = document.querySelector("#transcriptFile");
const analyzeButton = document.querySelector("#analyzeButton");
const clearTranscript = document.querySelector("#clearTranscript");
const intakeStatus = document.querySelector("#intakeStatus");
const logoutButton = document.querySelector("#logout");

const saveTimers = new Map();

let items = [];
let aiConfigured = false;
let modelName = "";
let draftOpen = false;
let confirmDeleteId = "";

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
      if (!response.ok) {
        setStatus("save failed", "bad");
        return;
      }
      setStatus("saved");
    }, 450),
  );
}

function autosize(textarea) {
  textarea.style.height = "0px";
  textarea.style.height = `${Math.min(220, Math.max(36, textarea.scrollHeight))}px`;
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
    render();
    return;
  }
  const response = await fetch(`/api/todo/items/${item.id}`, { method: "DELETE" });
  if (!response.ok) {
    setStatus("delete failed", "bad");
    return;
  }
  items = items.filter((entry) => entry.id !== item.id);
  confirmDeleteId = "";
  render();
  setStatus("deleted");
}

function buildDetailsCell(item) {
  const cell = document.createElement("td");
  cell.className = "details-cell";

  const top = document.createElement("div");
  top.className = "row-top";
  top.append(makeStateSelect(item));

  const source = document.createElement("span");
  source.className = "source-chip";
  const sourceParts = [item.sourceSpeaker, item.confidence].filter(Boolean);
  source.textContent = sourceParts.join(" · ") || "manual";
  top.append(source);

  const deleteButton = document.createElement("button");
  deleteButton.className = confirmDeleteId === item.id ? "delete-row is-confirming" : "delete-row";
  deleteButton.type = "button";
  deleteButton.setAttribute("aria-label", confirmDeleteId === item.id ? "confirm delete row" : "delete row");
  deleteButton.textContent = confirmDeleteId === item.id ? "delete?" : "×";
  deleteButton.addEventListener("click", () => deleteItem(item));
  top.append(deleteButton);

  const task = makeInput(item, "task", "task-input", true);
  const details = makeInput(item, "details", "details-input", true);
  cell.append(top, task, details);

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

function buildDateCell(item) {
  const cell = document.createElement("td");
  const input = document.createElement("input");
  input.className = "date-input";
  input.type = "date";
  input.value = safeText(item.dateAdded).slice(0, 10);
  input.setAttribute("aria-label", "date added");
  input.addEventListener("input", () => {
    item.dateAdded = input.value;
    scheduleSave(item.id, { dateAdded: input.value });
  });
  cell.append(input);
  return cell;
}

function buildTimeCell(item) {
  const cell = document.createElement("td");
  cell.append(makeInput(item, "timeEstimate", "time-input"));
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

function buildWhyCell(item) {
  const cell = document.createElement("td");
  cell.className = "why-cell";
  cell.append(makeInput(item, "why", "why-input", true));
  return cell;
}

function buildRow(item) {
  const row = document.createElement("tr");
  row.className = `todo-row state-${item.state || "review"}`;
  row.dataset.id = item.id;
  row.append(
    buildDetailsCell(item),
    buildDateCell(item),
    buildTimeCell(item),
    buildScoreCell(item, "easeScore", "ease score"),
    buildScoreCell(item, "disneyScore", "disney score"),
    buildTotalCell(item),
    buildWhyCell(item),
  );
  return row;
}

async function createManualItem(row) {
  const task = row.querySelector("[data-new='task']").value.trim();
  if (!task) {
    row.querySelector("[data-new='task']").focus();
    return;
  }
  const payload = {
    task,
    details: row.querySelector("[data-new='details']").value.trim(),
    dateAdded: row.querySelector("[data-new='date']").value,
    timeEstimate: row.querySelector("[data-new='time']").value.trim(),
    easeScore: scoreValue(row.querySelector("[data-new='ease']").value),
    disneyScore: scoreValue(row.querySelector("[data-new='disney']").value),
    why: row.querySelector("[data-new='why']").value.trim(),
  };
  const response = await fetch("/api/todo/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    setStatus("row save failed", "bad");
    return;
  }
  const data = await response.json();
  items.push(data.item);
  draftOpen = false;
  render();
  setStatus("row saved");
}

function buildDraftRow() {
  const row = document.createElement("tr");
  row.className = "draft-row";

  const details = document.createElement("td");
  const task = document.createElement("textarea");
  task.className = "task-input";
  task.placeholder = "todo item";
  task.dataset.new = "task";
  task.addEventListener("input", () => autosize(task));

  const context = document.createElement("textarea");
  context.className = "details-input";
  context.placeholder = "context";
  context.dataset.new = "details";
  context.addEventListener("input", () => autosize(context));

  const actions = document.createElement("div");
  actions.className = "draft-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "primary-button small";
  save.textContent = "save";
  save.addEventListener("click", () => createManualItem(row));
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "quiet-button small";
  cancel.textContent = "cancel";
  cancel.addEventListener("click", () => {
    draftOpen = false;
    render();
  });
  actions.append(save, cancel);
  details.append(task, context, actions);

  const date = document.createElement("td");
  const dateInput = document.createElement("input");
  dateInput.className = "date-input";
  dateInput.type = "date";
  dateInput.dataset.new = "date";
  dateInput.value = new Date().toISOString().slice(0, 10);
  date.append(dateInput);

  const time = document.createElement("td");
  const timeInput = document.createElement("input");
  timeInput.className = "time-input";
  timeInput.placeholder = "30 min";
  timeInput.dataset.new = "time";
  time.append(timeInput);

  const ease = document.createElement("td");
  const easeScore = document.createElement("input");
  easeScore.className = "score-input";
  easeScore.type = "number";
  easeScore.min = "0";
  easeScore.max = "100";
  easeScore.value = "50";
  easeScore.dataset.new = "ease";
  ease.append(easeScore);

  const disney = document.createElement("td");
  const disneyScore = document.createElement("input");
  disneyScore.className = "score-input";
  disneyScore.type = "number";
  disneyScore.min = "0";
  disneyScore.max = "100";
  disneyScore.value = "50";
  disneyScore.dataset.new = "disney";
  disney.append(disneyScore);

  const total = document.createElement("td");
  total.className = "total-cell";
  const totalNumber = document.createElement("span");
  totalNumber.className = "total-score";
  totalNumber.textContent = "100";
  total.append(totalNumber);
  const updateDraftTotal = () => {
    totalNumber.textContent = String(scoreValue(easeScore.value) + scoreValue(disneyScore.value));
  };
  easeScore.addEventListener("input", updateDraftTotal);
  disneyScore.addEventListener("input", updateDraftTotal);

  const why = document.createElement("td");
  const whyInput = document.createElement("textarea");
  whyInput.className = "why-input";
  whyInput.dataset.new = "why";
  whyInput.placeholder = "why it helps";
  whyInput.addEventListener("input", () => autosize(whyInput));
  why.append(whyInput);

  row.append(details, date, time, ease, disney, total, why);
  requestAnimationFrame(() => {
    autosize(task);
    task.focus();
  });
  return row;
}

function buildNewRowButton() {
  const row = document.createElement("tr");
  row.className = "new-row";
  const cell = document.createElement("td");
  cell.colSpan = 7;
  const button = document.createElement("button");
  button.className = "new-row-button";
  button.type = "button";
  button.textContent = "+ new row";
  button.addEventListener("click", () => {
    draftOpen = true;
    render();
  });
  cell.append(button);
  row.append(cell);
  return row;
}

function render() {
  const sorted = [...items].sort((a, b) => totalScore(b) - totalScore(a));
  const rows = sorted.map(buildRow);
  rows.push(draftOpen ? buildDraftRow() : buildNewRowButton());
  if (!rows.length) {
    rows.push(buildNewRowButton());
  }
  itemsEl.replaceChildren(...rows);
}

async function loadItems() {
  const response = await fetch("/api/todo/items");
  if (response.status === 401) {
    window.location.href = "/";
    return;
  }
  if (!response.ok) {
    setStatus("load failed", "bad");
    return;
  }
  const payload = await response.json();
  items = payload.items || [];
  aiConfigured = Boolean(payload.aiConfigured);
  modelName = payload.model || "";
  render();
  setStatus(aiConfigured ? `ready · ${modelName}` : "AI key missing", aiConfigured ? "" : "bad");
}

async function analyzeTranscript() {
  const transcript = transcriptInput.value.trim();
  if (!transcript) {
    transcriptInput.focus();
    setStatus("paste or upload a transcript", "bad");
    return;
  }
  analyzeButton.disabled = true;
  setStatus("analyzing");
  try {
    const response = await fetch("/api/todo/transcripts/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: transcriptName.value.trim(), transcript }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setStatus(payload.detail || "analysis failed", "bad");
      return;
    }
    items = payload.allItems || items;
    render();
    transcriptInput.value = "";
    transcriptName.value = "";
    transcriptFile.value = "";
    const count = payload.items?.length || 0;
    setStatus(count ? `saved ${count} rows` : "saved transcript, no supported todos found");
  } finally {
    analyzeButton.disabled = false;
  }
}

transcriptFile.addEventListener("change", async () => {
  const file = transcriptFile.files?.[0];
  if (!file) return;
  const text = await file.text();
  transcriptInput.value = text;
  if (!transcriptName.value.trim()) transcriptName.value = file.name.replace(/\.[^.]+$/, "");
  setStatus(`${file.name} loaded`);
});

analyzeButton.addEventListener("click", analyzeTranscript);
clearTranscript.addEventListener("click", () => {
  transcriptInput.value = "";
  transcriptName.value = "";
  transcriptFile.value = "";
  setStatus("cleared");
});

logoutButton.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/";
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && document.activeElement === transcriptInput) {
    event.preventDefault();
    analyzeTranscript();
  }
});

loadItems();
