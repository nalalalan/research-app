const state = {
  items: [],
  summary: null,
};

const els = {
  form: document.getElementById("todo-form"),
  input: document.getElementById("todo-input"),
  save: document.getElementById("save-item"),
  status: document.getElementById("form-status"),
  list: document.getElementById("todo-list"),
  summaryLine: document.getElementById("summary-line"),
  updatedAt: document.getElementById("updated-at"),
  logout: document.getElementById("logout"),
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

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

function renderSummary() {
  const count = state.summary?.item_count ?? state.items.length;
  els.summaryLine.textContent = plural(count, "item");
  els.updatedAt.textContent = state.summary?.updated_at ? `updated ${formatDate(state.summary.updated_at)}` : "not updated";
}

function renderItems() {
  if (!state.items.length) {
    els.list.innerHTML = `<tr class="empty-row"><td>No items.</td></tr>`;
    return;
  }
  els.list.innerHTML = state.items.map((item) => `
    <tr class="todo-row">
      <td>
        <span>${escapeHtml(item.todo)}</span>
        <button type="button" data-delete="${escapeHtml(item.id)}" aria-label="delete todo">delete</button>
      </td>
    </tr>
  `).join("");
}

async function loadItems() {
  const response = await fetch("/api/todo/items");
  if (response.status === 401) {
    window.location.href = "/";
    return;
  }
  if (!response.ok) throw new Error("load failed");
  const payload = await response.json();
  state.items = payload.items || [];
  state.summary = payload.summary || null;
  renderSummary();
  renderItems();
}

async function saveItem(event) {
  event.preventDefault();
  els.status.textContent = "saving";
  els.save.disabled = true;
  try {
    const response = await fetch("/api/todo/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ todo: els.input.value }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "save failed");
    }
    els.input.value = "";
    els.status.textContent = "";
    await loadItems();
  } catch (error) {
    els.status.textContent = error.message || "save failed";
  } finally {
    els.save.disabled = false;
  }
}

async function deleteItem(id) {
  const response = await fetch(`/api/todo/items/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error("delete failed");
  await loadItems();
}

els.form.addEventListener("submit", saveItem);
els.list.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-delete]");
  if (!button) return;
  button.disabled = true;
  try {
    await deleteItem(button.dataset.delete);
  } catch (error) {
    button.disabled = false;
  }
});
els.logout.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/";
});

loadItems().catch(() => {
  els.summaryLine.textContent = "Load failed.";
});
