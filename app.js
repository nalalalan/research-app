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
  summaryLine: document.getElementById("summary-line"),
  todayCount: document.getElementById("today-count"),
  streakDays: document.getElementById("streak-days"),
  entryCount: document.getElementById("entry-count"),
  updatedAt: document.getElementById("updated-at"),
  logout: document.getElementById("logout"),
  categories: {
    imagineer: document.getElementById("cat-imagineer"),
    fluxcell: document.getElementById("cat-fluxcell"),
    sarrus: document.getElementById("cat-sarrus"),
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

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function linkHtml(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (!/^https?:\/\//i.test(text)) return escapeHtml(text);
  let label = text.replace(/^https?:\/\//i, "").replace(/\/$/, "");
  if (label.length > 46) label = `${label.slice(0, 43)}...`;
  return `<a href="${escapeHtml(text)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
}

function renderSummary() {
  const summary = state.summary || {};
  els.todayCount.textContent = summary.today_count ?? 0;
  els.streakDays.textContent = summary.streak_days ?? 0;
  els.entryCount.textContent = summary.entry_count ?? 0;
  els.updatedAt.textContent = summary.updated_at ? `updated ${formatDate(summary.updated_at)}` : "not updated";

  const latest = summary.latest;
  if (latest) {
    els.summaryLine.textContent = `${latest.category} ${latest.status || "moved"} ${formatDate(latest.created_at)}`;
  } else {
    els.summaryLine.textContent = "No entries yet.";
  }

  for (const key of Object.keys(els.categories)) {
    const item = summary.categories?.[key] || {};
    const latestItem = item.latest;
    els.categories[key].textContent = latestItem
      ? `${plural(item.count || 0, "entry")} · latest ${formatDate(latestItem.created_at)}`
      : "0 entries";
  }
}

function renderEntries() {
  if (!state.entries.length) {
    els.table.innerHTML = `<div class="empty-row">No entries yet.</div>`;
    return;
  }
  els.table.innerHTML = state.entries.map((entry) => `
    <article class="entry-row ${escapeHtml(entry.category)}">
      <div class="entry-time">
        <strong>${formatDate(entry.created_at)}</strong>
        <span>${escapeHtml(entry.category)} · ${escapeHtml(entry.status || "moved")}</span>
      </div>
      <div class="entry-work">${escapeHtml(entry.work)}</div>
      <div class="entry-artifact">${linkHtml(entry.artifact_url)}</div>
      <div class="entry-next">${escapeHtml(entry.next_step || "")}</div>
      <div class="entry-meta">
        <span>${entry.minutes ? `${entry.minutes}m` : ""}</span>
        <button type="button" data-delete="${escapeHtml(entry.id)}" aria-label="delete entry">delete</button>
      </div>
    </article>
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
  renderSummary();
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
    const payload = await response.json();
    state.summary = payload.summary || null;
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

