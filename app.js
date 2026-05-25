const itemsEl = document.querySelector("#items");
const emptyEl = document.querySelector("#empty");
const form = document.querySelector("#add-form");
const newItemInput = document.querySelector("#new-item");
const saveTimers = new Map();

let items = [];

function scrollStatusToBottom(textarea) {
  requestAnimationFrame(() => {
    textarea.scrollTop = textarea.scrollHeight;
  });
}

function scheduleSave(id, patch) {
  const existing = saveTimers.get(id);
  if (existing) clearTimeout(existing);
  saveTimers.set(
    id,
    setTimeout(async () => {
      saveTimers.delete(id);
      await fetch(`/api/todo/items/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
    }, 500),
  );
}

function buildRow(item) {
  const tr = document.createElement("tr");
  tr.dataset.id = item.id;

  const itemCell = document.createElement("td");
  itemCell.className = "item-cell";

  const itemWrap = document.createElement("div");
  itemWrap.className = "item-wrap";

  const nameInput = document.createElement("input");
  nameInput.className = "item-input";
  nameInput.value = item.item || "";
  nameInput.setAttribute("aria-label", "item");
  nameInput.addEventListener("input", () => {
    scheduleSave(item.id, { item: nameInput.value });
  });

  const deleteButton = document.createElement("button");
  deleteButton.className = "delete";
  deleteButton.type = "button";
  deleteButton.setAttribute("aria-label", "delete item");
  deleteButton.textContent = "x";
  deleteButton.addEventListener("click", async () => {
    const response = await fetch(`/api/todo/items/${item.id}`, { method: "DELETE" });
    if (response.ok) {
      items = items.filter((entry) => entry.id !== item.id);
      render();
    }
  });

  itemWrap.append(nameInput, deleteButton);
  itemCell.append(itemWrap);

  const statusCell = document.createElement("td");
  statusCell.className = "status-cell";

  const textarea = document.createElement("textarea");
  textarea.className = "status-window";
  textarea.value = item.status || "";
  textarea.spellcheck = true;
  textarea.setAttribute("aria-label", "status");
  textarea.addEventListener("input", () => {
    scrollStatusToBottom(textarea);
    scheduleSave(item.id, { status: textarea.value });
  });
  textarea.addEventListener("focus", () => scrollStatusToBottom(textarea));

  statusCell.append(textarea);
  tr.append(itemCell, statusCell);
  scrollStatusToBottom(textarea);
  return tr;
}

function render() {
  itemsEl.replaceChildren(...items.map(buildRow));
  emptyEl.hidden = items.length !== 0;
}

async function loadItems() {
  const response = await fetch("/api/todo/items");
  if (response.status === 401) {
    window.location.href = "/";
    return;
  }
  const payload = await response.json();
  items = payload.items || [];
  render();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = newItemInput.value.trim();
  if (!value) return;
  const response = await fetch("/api/todo/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item: value, status: "" }),
  });
  if (!response.ok) return;
  const payload = await response.json();
  items.push(payload.item);
  newItemInput.value = "";
  render();
  const row = itemsEl.querySelector(`tr[data-id="${payload.item.id}"]`);
  row?.querySelector(".status-window")?.focus();
});

loadItems();
