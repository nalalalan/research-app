const itemsEl = document.querySelector("#items");
const doneItemsEl = document.querySelector("#doneItems");
const doneShell = document.querySelector("#doneShell");
const transcriptsEl = document.querySelector("#transcripts");
const transcriptInput = document.querySelector("#transcriptInput");
const analyzeButton = document.querySelector("#analyzeButton");
const intakeStatus = document.querySelector("#intakeStatus");
const todoCountEl = document.querySelector("#todoCount");
const doneCountEl = document.querySelector("#doneCount");
const sortButtons = [...document.querySelectorAll(".sort-button")];
const categoryFilterButtons = [...document.querySelectorAll("[data-category-filter]")];

const saveTimers = new Map();
const CATEGORIES = ["paper", "prototype", "phd"];
const PHD_KEYWORDS = ["phd", "ph.d", "proposal", "dissertation", "thesis", "committee", "defense", "qualifying"];
const PAPER_ARTIFACT_KEYWORDS = [
  "manuscript",
  "abstract",
  "caption",
  "citation",
  "reference",
  "references",
  "submission",
  "journal",
  "reviewer",
  "figure",
  "results",
  "discussion",
  "methods",
  "section",
  "latex",
  "overleaf",
  "pdf",
  "chi",
  "picture",
  "pictures",
  "photo",
  "photos",
  "image",
  "images",
  "cartoon",
  "diagram",
  "drawing",
  "illustration",
  "plot",
  "plots",
  "graph",
  "graphs",
  "chart",
  "charts",
  "text",
  "clarification",
  "explanation",
  "claim",
  "characterization",
  "visual",
];
const PAPER_HARD_OVERRIDE_KEYWORDS = [
  "manuscript",
  "abstract",
  "caption",
  "citation",
  "reference",
  "references",
  "journal",
  "reviewer",
  "figure",
  "latex",
  "overleaf",
  "pdf",
  "chi",
  "picture",
  "pictures",
  "photo",
  "photos",
  "image",
  "images",
  "cartoon",
  "diagram",
  "drawing",
  "illustration",
  "plot",
  "plots",
  "graph",
  "graphs",
  "chart",
  "charts",
  "text",
  "clarification",
  "explanation",
  "claim",
  "characterization",
  "visual",
];
const PAPER_OUTPUT_KEYWORDS = [
  ...PAPER_HARD_OVERRIDE_KEYWORDS,
  "writeup",
  "write-up",
  "video explanation",
  "result explanation",
];
const PAPER_ACTION_KEYWORDS = [
  "add",
  "check",
  "describe",
  "draw",
  "edit",
  "explain",
  "clarify",
  "include",
  "insert",
  "make",
  "plot",
  "present",
  "revise",
  "review",
  "show",
  "submit",
  "update",
  "write",
];
const PROTOTYPE_KEYWORDS = [
  "prototype",
  "build",
  "valve",
  "epm",
  "magnet",
  "magnetic",
  "manifold",
  "hardware",
  "cad",
  "fabricat",
  "print",
  "assembly",
  "actuator",
  "mechanism",
  "test",
  "measurement",
  "experiment",
  "comsol",
  "simulation",
];

let items = [];
let transcripts = [];
let aiConfigured = false;
let modelName = "";
let confirmDeleteId = "";
let confirmTranscriptDeleteId = "";
let retryingTranscriptId = "";
let sortState = { key: "", direction: "desc" };
const categoryFilters = new Set();

function setStatus(text, tone = "") {
  intakeStatus.textContent = text;
  intakeStatus.dataset.tone = tone;
}

function safeText(value) {
  return value == null ? "" : repairDisplayText(String(value));
}

function repairDisplayText(value) {
  return value
    .replace(/\u00e2\u0080\u0099/g, "'")
    .replace(/\u00e2\u0080\u0098/g, "'")
    .replace(/\u00e2\u0080\u009c/g, '"')
    .replace(/\u00e2\u0080\u009d/g, '"')
    .replace(/\u00e2\u0080\u00a6/g, "...")
    .replace(/\u00c2\u00b0/g, " degrees")
    .replace(/\ufffd/g, "'");
}

function scoreValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number)));
}

function totalScore(item) {
  return scoreValue(item.easeScore) + scoreValue(item.disneyScore);
}

function addedTimeValue(item) {
  const createdTime = Date.parse(safeText(item.createdAt));
  if (Number.isFinite(createdTime)) return createdTime;
  const dateTime = Date.parse(safeText(item.dateAdded));
  return Number.isFinite(dateTime) ? dateTime : 0;
}

function doneTimeValue(item) {
  const doneTime = Date.parse(safeText(item.doneAt));
  if (Number.isFinite(doneTime)) return doneTime;
  const updatedTime = Date.parse(safeText(item.updatedAt));
  if (Number.isFinite(updatedTime)) return updatedTime;
  return addedTimeValue(item);
}

function isDone(item) {
  return safeText(item.state) === "done";
}

function includesAny(text, keywords) {
  return keywords.some((keyword) => text.includes(keyword));
}

function includesActionAndOutput(text) {
  return includesAny(text, PAPER_ACTION_KEYWORDS) && includesAny(text, PAPER_OUTPUT_KEYWORDS);
}

function inferredCategory(item) {
  const taskText = safeText(item.task).toLowerCase();
  const evidenceText = Array.isArray(item.evidence) ? item.evidence.map(safeText).join(" ") : "";
  const text = [
    taskText,
    safeText(item.details),
    safeText(item.why),
    safeText(item.quote),
    evidenceText,
  ].join(" ").toLowerCase();
  const hasPhd = includesAny(text, PHD_KEYWORDS);
  const taskIsPhdAdmin = includesAny(taskText, PHD_KEYWORDS);
  const hasPaperArtifact = includesAny(text, PAPER_ARTIFACT_KEYWORDS);
  const hasPaperHardOverride = includesAny(text, PAPER_HARD_OVERRIDE_KEYWORDS);
  const hasPaperNamed = text.includes("paper") || text.includes("manuscript");
  const hasPaperAction = includesAny(text, PAPER_ACTION_KEYWORDS);
  const taskIsPaperOutput = includesActionAndOutput(taskText);
  if ((hasPaperArtifact && !taskIsPhdAdmin) || hasPaperHardOverride || (hasPaperNamed && hasPaperAction && !taskIsPhdAdmin) || taskIsPaperOutput) return "paper";
  if (includesAny(text, PROTOTYPE_KEYWORDS)) return "prototype";
  if (hasPhd) return "phd";
  return "";
}

function itemCategory(item) {
  const inferred = inferredCategory(item);
  if (inferred) return inferred;
  const category = safeText(item.category).trim().toLowerCase();
  return CATEGORIES.includes(category) ? category : "phd";
}

function passesCategoryFilter(item) {
  return categoryFilters.size === 0 || categoryFilters.has(itemCategory(item));
}

function todoCountLabel(count) {
  const safeCount = Math.max(0, Number(count) || 0);
  return `${safeCount} ${safeCount === 1 ? "todo" : "todos"}`;
}

function analysisResultLabel(payload) {
  const added = Math.max(0, Number(payload.addedItemCount) || 0);
  const merged = Math.max(0, Number(payload.mergedItemCount) || 0);
  if (added && merged) return `added ${todoCountLabel(added)}, updated ${todoCountLabel(merged)}`;
  if (added) return `added ${todoCountLabel(added)}`;
  if (merged) return `updated ${todoCountLabel(merged)}`;
  const fallback = Math.max(0, Number(payload.items?.length) || 0);
  return fallback ? `updated ${todoCountLabel(fallback)}` : "saved transcription, no supported todos found";
}

function updateTodoCount(activeCount, doneCount) {
  if (todoCountEl) {
    todoCountEl.textContent = `${activeCount} active / ${doneCount} done`;
  }
  if (doneCountEl) {
    doneCountEl.textContent = `${doneCount} done`;
  }
}

function formatDate(value) {
  const text = safeText(value);
  if (!text) return "";
  return text.slice(0, 10);
}

function formatAddedAt(item) {
  const created = safeText(item.createdAt);
  const time = Date.parse(created);
  if (Number.isFinite(time)) {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZone: "America/New_York",
    }).format(new Date(time));
  }
  return formatDate(item.dateAdded);
}

function compactParts(parts) {
  return parts
    .map((part) => safeText(part).trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function sentencePart(value) {
  const text = safeText(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  return /[.!?](?:["')\]]*)$/.test(text) ? text : `${text}.`;
}

function firstWord(value) {
  return (safeText(value).toLowerCase().match(/[a-z]+/) || [""])[0];
}

function significantTokens(value) {
  const stop = new Set([
    "about",
    "after",
    "again",
    "and",
    "are",
    "case",
    "could",
    "does",
    "each",
    "from",
    "have",
    "into",
    "like",
    "near",
    "only",
    "should",
    "that",
    "the",
    "this",
    "using",
    "where",
    "with",
  ]);
  return new Set(
    safeText(value)
      .toLowerCase()
      .match(/[a-z0-9]+/g)
      ?.filter((token) => token.length > 2 && !stop.has(token)) || [],
  );
}

function tokenOverlap(left, right) {
  const leftTokens = significantTokens(left);
  const rightTokens = significantTokens(right);
  const smaller = Math.min(leftTokens.size, rightTokens.size);
  if (!smaller) return 0;
  let shared = 0;
  leftTokens.forEach((token) => {
    if (rightTokens.has(token)) shared += 1;
  });
  return shared / smaller;
}

function startsWithRepeatedAction(left, right) {
  const actionVerbs = new Set([
    "add",
    "check",
    "draw",
    "fix",
    "make",
    "replace",
    "revise",
    "swap",
    "update",
    "write",
  ]);
  const leftWord = firstWord(left);
  return leftWord && leftWord === firstWord(right) && actionVerbs.has(leftWord) && tokenOverlap(left, right) >= 0.3;
}

function detailsReplaceRepeatedTask(task, details) {
  if (safeText(details).length < 45) return false;
  return startsWithRepeatedAction(task, details);
}

function splitSentences(value) {
  return safeText(value)
    .replace(/\s+/g, " ")
    .trim()
    .split(/(?<=[.!?])\s+(?=[A-Z0-9])/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
}

function removeRepeatedActionSentences(task, details) {
  const sentences = splitSentences(details);
  if (sentences.length < 2) return details;
  const filtered = sentences.filter((sentence) => !startsWithRepeatedAction(task, sentence));
  return filtered.length && filtered.length !== sentences.length ? filtered.join(" ") : details;
}

function polishedParts(parts) {
  const seen = new Set();
  const output = [];
  parts.forEach((part) => {
    const sentence = sentencePart(part);
    if (!sentence) return;
    const key = sentence.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    if (!key || seen.has(key)) return;
    seen.add(key);
    output.push(sentence);
  });
  return output.join(" ");
}

function fixBody(item) {
  const details = removeRepeatedActionSentences(item.task, item.details);
  const parts = detailsReplaceRepeatedTask(item.task, details) ? [details, item.why] : [item.task, details, item.why];
  return polishedParts(parts);
}

function splitFixBody(value) {
  return {
    task: safeText(value).replace(/\s+/g, " ").trim(),
    details: "",
    why: "",
  };
}

function fixMeta(item) {
  return [itemCategory(item), formatAddedAt(item), safeText(item.timeEstimate).trim()]
    .filter(Boolean)
    .join(" / ");
}

function quoteBody(item) {
  const quote = safeText(item.evidence?.length ? item.evidence[0] : "").trim();
  if (!quote) return "no quote saved";
  const speaker = safeText(item.sourceSpeaker).trim();
  if (!speaker) return quote;
  const normalizedQuote = quote.toLowerCase();
  const normalizedSpeaker = speaker.toLowerCase();
  if (normalizedQuote.startsWith(`${normalizedSpeaker}:`) || normalizedQuote.startsWith(`${normalizedSpeaker} -`)) {
    return quote;
  }
  return `${speaker}: ${quote}`;
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
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    setStatus("save failed", "bad");
    return;
  }
  Object.assign(item, payload.item || { state: "done" });
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
  setStatus(removed ? `deleted transcription and ${todoCountLabel(removed)}` : "deleted transcription");
}

async function retryTranscript(entry) {
  retryingTranscriptId = entry.id;
  renderTranscripts();
  setStatus("retrying analysis");
  try {
    const response = await fetch(`/api/todo/transcripts/${entry.id}/retry`, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      await loadItems({ setReadyStatus: false });
      setStatus(payload.detail || "analysis failed; saved transcription can be retried", "bad");
      return;
    }
    items = payload.allItems || items;
    transcripts = payload.allTranscripts || transcripts;
    render();
    setStatus(analysisResultLabel(payload));
  } catch {
    await loadItems({ setReadyStatus: false });
    setStatus("analysis failed; saved transcription can be retried", "bad");
  } finally {
    retryingTranscriptId = "";
    renderTranscripts();
  }
}

function buildTodoCell(item) {
  const cell = document.createElement("td");
  cell.className = "todo-cell";

  const actions = document.createElement("div");
  actions.className = "row-actions";

  if (!isDone(item)) {
    const doneButton = document.createElement("button");
    doneButton.className = "done-row";
    doneButton.type = "button";
    doneButton.setAttribute("aria-label", "mark row done");
    doneButton.textContent = "done";
    doneButton.addEventListener("click", () => markDone(item));
    actions.append(doneButton);
  }

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

  const meta = document.createElement("div");
  meta.className = "todo-meta";
  meta.textContent = fixMeta(item);

  const todo = document.createElement("textarea");
  todo.className = "fix-input";
  todo.value = fixBody(item);
  todo.spellcheck = true;
  todo.setAttribute("aria-label", "todo");
  todo.addEventListener("input", () => {
    const patch = splitFixBody(todo.value);
    item.task = patch.task;
    item.details = patch.details;
    item.why = patch.why;
    autosize(todo);
    scheduleSave(item.id, patch);
  });
  requestAnimationFrame(() => autosize(todo));
  if (meta.textContent) fixSection.append(meta);
  fixSection.append(todo);

  const quoteSection = document.createElement("section");
  quoteSection.className = "todo-section";

  const quote = document.createElement("blockquote");
  quote.className = "quote-block";
  quote.textContent = quoteBody(item);

  quoteSection.append(quote);
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
  if (key === "todo") return addedTimeValue(item);
  if (key === "ease") return scoreValue(item.easeScore);
  if (key === "disney") return scoreValue(item.disneyScore);
  return totalScore(item);
}

function sortedItems(itemList = items) {
  const activeKey = sortState.key || "total";
  const direction = sortState.key ? sortState.direction : "desc";
  return [...itemList].sort((a, b) => {
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

function updateCategoryFilters() {
  categoryFilterButtons.forEach((button) => {
    const category = safeText(button.dataset.categoryFilter).trim().toLowerCase();
    const active = categoryFilters.has(category);
    button.dataset.active = active ? "true" : "false";
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function renderItems() {
  updateSortHeaders();
  updateCategoryFilters();
  const activeItems = items.filter((item) => !isDone(item) && passesCategoryFilter(item));
  const doneItems = items
    .filter((item) => isDone(item) && passesCategoryFilter(item))
    .sort((a, b) => doneTimeValue(b) - doneTimeValue(a));
  updateTodoCount(activeItems.length, doneItems.length);
  const sorted = sortedItems(activeItems);
  if (!sorted.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = items.length ? "no active todo rows" : "no todo rows yet";
    row.append(cell);
    itemsEl.replaceChildren(row);
  } else {
    itemsEl.replaceChildren(...sorted.map(buildRow));
  }
  if (doneShell) doneShell.hidden = !doneItems.length;
  if (doneItemsEl) {
    doneItemsEl.replaceChildren(...doneItems.map(buildRow));
  }
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
    const statusText =
      entry.status === "failed" ? "failed" : entry.status === "analyzing" ? "analysis running" : "";
    const parts = [
      todoCountLabel(entry.itemCount || 0),
      `${(entry.characterCount || 0).toLocaleString()} chars`,
      statusText,
    ];
    meta.textContent = parts.filter(Boolean).join(" / ");

    const summary = document.createElement("div");
    summary.className = entry.status === "failed" ? "transcript-summary is-error" : "transcript-summary";
    summary.textContent =
      entry.status === "failed"
        ? entry.error || "analysis failed; saved transcription can be retried"
        : entry.summary || "";

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
    if (entry.status === "failed") {
      const retryButton = document.createElement("button");
      retryButton.className = "transcript-retry";
      retryButton.type = "button";
      retryButton.disabled = retryingTranscriptId === entry.id;
      retryButton.textContent = retryingTranscriptId === entry.id ? "retrying" : "retry";
      retryButton.addEventListener("click", () => retryTranscript(entry));
      actions.append(retryButton);
    }
    actions.append(link, deleteButton);

    card.append(title, meta);
    if (summary.textContent) card.append(summary);
    card.append(actions);
    return card;
  });
  transcriptsEl.replaceChildren(...cards);
}

function render() {
  renderItems();
  renderTranscripts();
}

async function loadItems(options = {}) {
  const setReadyStatus = options.setReadyStatus !== false;
  const response = await fetch("/api/todo/items");
  if (!response.ok) {
    if (setReadyStatus) setStatus("load failed", "bad");
    return false;
  }
  const payload = await response.json();
  items = payload.items || [];
  transcripts = payload.transcripts || [];
  aiConfigured = Boolean(payload.aiConfigured);
  modelName = payload.model || "";
  render();
  if (setReadyStatus) setStatus(aiConfigured ? `ready / ${modelName}` : "AI key missing", aiConfigured ? "" : "bad");
  return true;
}

async function reconcileAnalysisFailure(beforeItemCount, beforeTranscriptIds, message) {
  const loaded = await loadItems({ setReadyStatus: false });
  if (loaded) {
    const added = Math.max(0, items.length - beforeItemCount);
    const newTranscripts = transcripts.filter((entry) => !beforeTranscriptIds.has(entry.id));
    const completeTranscript = newTranscripts.find((entry) => entry.status === "complete");
    const failedTranscript = newTranscripts.find((entry) => entry.status === "failed");
    if (added || completeTranscript) {
      setStatus(added ? `added ${todoCountLabel(added)}` : "saved transcription, no supported todos found");
      return;
    }
    if (failedTranscript) {
      setStatus(failedTranscript.error || message || "analysis failed; saved transcription can be retried", "bad");
      return;
    }
  }
  setStatus(message || "analysis failed", "bad");
}

async function analyzeTranscript() {
  const transcript = transcriptInput.value.trim();
  if (!transcript) {
    transcriptInput.focus();
    setStatus("paste a transcription", "bad");
    return;
  }
  const beforeItemCount = items.length;
  const beforeTranscriptIds = new Set(transcripts.map((entry) => entry.id));
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
      await reconcileAnalysisFailure(beforeItemCount, beforeTranscriptIds, payload.detail || "analysis failed");
      return;
    }
    items = payload.allItems || items;
    transcripts = payload.allTranscripts || transcripts;
    render();
    transcriptInput.value = "";
    setStatus(analysisResultLabel(payload));
  } catch {
    await reconcileAnalysisFailure(beforeItemCount, beforeTranscriptIds, "analysis failed");
  } finally {
    analyzeButton.disabled = false;
  }
}

analyzeButton.addEventListener("click", analyzeTranscript);

function applySortClick(key) {
  if (sortState.key === key) {
    sortState.direction = sortState.direction === "desc" ? "asc" : "desc";
  } else {
    sortState = { key, direction: "desc" };
  }
  renderItems();
}

sortButtons.forEach((button) => {
  const key = button.dataset.sortKey || "total";
  const th = button.closest("th");
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    applySortClick(key);
  });
  if (th) {
    th.addEventListener("click", () => applySortClick(key));
  }
});

categoryFilterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const category = safeText(button.dataset.categoryFilter).trim().toLowerCase();
    if (!CATEGORIES.includes(category)) return;
    if (categoryFilters.has(category)) {
      categoryFilters.delete(category);
    } else {
      categoryFilters.add(category);
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
