const CATEGORIES = ["imagineer", "fluxcell", "sarrus"];

const state = {
  entries: [],
  summary: null,
};

const els = {
  form: document.getElementById("entry-form"),
  category: document.getElementById("category"),
  status: document.getElementById("status"),
  minutes: document.getElementById("minutes"),
  work: document.getElementById("work"),
  artifact: document.getElementById("artifact"),
  nextStep: document.getElementById("next-step"),
  formStatus: document.getElementById("form-status"),
  save: document.getElementById("save-entry"),
  table: document.getElementById("entry-table"),
  tableCount: document.getElementById("table-count"),
  summaryLine: document.getElementById("summary-line"),
  todayCount: document.getElementById("today-count"),
  todayMinutes: document.getElementById("today-minutes"),
  streakDays: document.getElementById("streak-days"),
  entryCount: document.getElementById("entry-count"),
  totalMinutes: document.getElementById("total-minutes"),
  openSteps: document.getElementById("open-steps"),
  latestStatus: document.getElementById("latest-status"),
  updatedAt: document.getElementById("updated-at"),
  logout: document.getElementById("logout"),
  categories: {
    imagineer: document.getElementById("cat-imagineer"),
    fluxcell: document.getElementById("cat-fluxcell"),
    sarrus: document.getElementById("cat-sarrus"),
  },
  bars: {
    imagineer: document.getElementById("bar-imagineer"),
    fluxcell: document.getElementById("bar-fluxcell"),
    sarrus: document.getElementById("bar-sarrus"),
  },
};

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function plural(value, word) {
  return `${value} ${word}${value === 1 ? "" : "s"}`;
}

function formatMinutes(value) {
  const minutes = Number(value || 0);
  return `${minutes} min`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function linkHtml(value) {
  const text = String(value || "").trim();
  if (!text) return `<span class="muted">-</span>`;
  if (!/^https?:\/\//i.test(text)) return escapeHtml(text);
  let label = text.replace(/^https?:\/\//i, "").replace(/\/$/, "");
  if (label.length > 34) label = `${label.slice(0, 31)}...`;
  return `<a href="${escapeHtml(text)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
}

function entryDay(entry) {
  return entry.day || "";
}

function sumMinutes(entries) {
  return entries.reduce((total, entry) => total + Number(entry.minutes || 0), 0);
}

function renderAnalysis() {
  const summary = state.summary || {};
  const entries = state.entries || [];
  const latest = entries[0] || summary.latest;
  const today = summary.today;
  const todayEntries = entries.filter((entry) => entryDay(entry) === today);
  const totalMinutes = summary.minutes ?? sumMinutes(entries);
  const openSteps = entries.filter((entry) => String(entry.next_step || "").trim()).length;
  const maxCategoryCount = Math.max(1, ...CATEGORIES.map((key) => summary.categories?.[key]?.count || 0));

  els.todayCount.textContent = summary.today_count ?? todayEntries.length;
  els.todayMinutes.textContent = formatMinutes(sumMinutes(todayEntries));
  els.streakDays.textContent = summary.streak_days ?? 0;
  els.entryCount.textContent = summary.entry_count ?? entries.length;
  els.totalMinutes.textContent = formatMinutes(totalMinutes);
  els.openSteps.textContent = openSteps;
  els.tableCount.textContent = plural(entries.length, "row");
  els.updatedAt.textContent = summary.updated_at ? `updated ${formatDate(summary.updated_at)}` : "not updated";

  if (latest) {
    els.summaryLine.textContent = `${latest.category} ${latest.status || "moved"} at ${formatDate(latest.created_at)}`;
    els.latestStatus.textContent = `${latest.category} ${latest.status || "moved"}`;
  } else {
    els.summaryLine.textContent = "No rows yet.";
    els.latestStatus.textContent = "no entries";
  }

  for (const key of CATEGORIES) {
    const item = summary.categories?.[key] || {};
    const count = item.count || 0;
    const minutes = item.minutes || 0;
    const todayCount = item.today_count || 0;
    els.categories[key].textContent = `${plural(count, "entry")} / ${formatMinutes(minutes)} / ${todayCount} today`;
    els.bars[key].style.width = `${Math.round((count / maxCategoryCount) * 100)}%`;
  }
}

function renderEntries() {
  if (!state.entries.length) {
    els.table.innerHTML = `
      <tr class="empty-row">
        <td colspan="8">No rows yet.</td>
      </tr>
    `;
    return;
  }

  els.table.innerHTML = state.entries.map((entry) => `
    <tr class="entry-row ${escapeHtml(entry.category)}">
      <td data-label="date">
        <strong>${formatDate(entry.created_at)}</strong>
        <span>${escapeHtml(entry.day || "")}</span>
      </td>
      <td data-label="category"><span class="category-pill">${escapeHtml(entry.category)}</span></td>
      <td data-label="status">${escapeHtml(entry.status || "moved")}</td>
      <td data-label="minutes">${entry.minutes ? formatMinutes(entry.minutes) : `<span class="muted">-</span>`}</td>
      <td data-label="what moved" class="work-cell">${escapeHtml(entry.work)}</td>
      <td data-label="artifact" class="artifact-cell">${linkHtml(entry.artifact_url)}</td>
      <td data-label="next step">${escapeHtml(entry.next_step || "") || `<span class="muted">-</span>`}</td>
      <td data-label="action" class="action-cell">
        <button type="button" data-delete="${escapeHtml(entry.id)}" aria-label="delete row">delete</button>
      </td>
    </tr>
  `).join("");
}

async function loadEntries() {
  const response = await fetch("/api/research/entries");
  if (response.status === 401) {
    window.location.href = "/";
    return;
  }
  if (!response.ok) throw new Error("entries failed");
  const payload = await response.json();
  state.entries = payload.entries || [];
  state.summary = payload.summary || null;
  renderAnalysis();
  renderEntries();
}

async function saveEntry(event) {
  event.preventDefault();
  els.formStatus.textContent = "saving";
  els.save.disabled = true;
  try {
    const minutes = els.minutes.value ? Number(els.minutes.value) : null;
    const response = await fetch("/api/research/entries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: els.category.value,
        status: els.status.value,
        minutes,
        work: els.work.value,
        artifact_url: els.artifact.value,
        next_step: els.nextStep.value,
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "save failed");
    }
    els.form.reset();
    els.category.value = "imagineer";
    els.status.value = "moved";
    els.formStatus.textContent = "saved";
    await loadEntries();
  } catch (error) {
    els.formStatus.textContent = error.message || "save failed";
  } finally {
    els.save.disabled = false;
  }
}

async function deleteEntry(id) {
  const response = await fetch(`/api/research/entries/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error("delete failed");
  await loadEntries();
  els.formStatus.textContent = "";
}

els.form.addEventListener("submit", saveEntry);
els.table.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-delete]");
  if (!button) return;
  button.disabled = true;
  try {
    await deleteEntry(button.dataset.delete);
  } catch (error) {
    button.disabled = false;
  }
});
els.logout.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/";
});

loadEntries().catch(() => {
  els.summaryLine.textContent = "Load failed.";
});
