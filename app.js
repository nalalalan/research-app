const itemsEl = document.querySelector("#items");
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

async function createItem(row) {
  const itemInput = row.querySelector(".new-item-input");
  const statusInput = row.querySelector(".new-status-window");
  const value = itemInput.value.trim();
  if (!value) {
    itemInput.focus();
    return;
  }
  const response = await fetch("/api/todo/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item: value, status: statusInput.value }),
  });
  if (!response.ok) return;
  const payload = await response.json();
  items.push(payload.item);
  render(payload.item.id);
}

function buildNewRow() {
  const tr = document.createElement("tr");
  tr.className = "new-item-row";

  const itemCell = document.createElement("td");
  itemCell.className = "item-cell";

  const itemWrap = document.createElement("div");
  itemWrap.className = "item-wrap";

  const itemInput = document.createElement("input");
  itemInput.className = "item-input new-item-input";
  itemInput.placeholder = "new item";
  itemInput.autocomplete = "off";
  itemInput.setAttribute("aria-label", "new item");
  itemInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createItem(tr);
    }
  });

  const addButton = document.createElement("button");
  addButton.className = "add-item";
  addButton.type = "button";
  addButton.setAttribute("aria-label", "add item");
  addButton.textContent = "+";
  addButton.addEventListener("click", () => createItem(tr));

  itemWrap.append(itemInput, addButton);
  itemCell.append(itemWrap);

  const statusCell = document.createElement("td");
  statusCell.className = "status-cell";

  const textarea = document.createElement("textarea");
  textarea.className = "status-window new-status-window";
  textarea.placeholder = "status";
  textarea.spellcheck = true;
  textarea.setAttribute("aria-label", "new status");
  textarea.addEventListener("input", () => scrollStatusToBottom(textarea));
  textarea.addEventListener("focus", () => scrollStatusToBottom(textarea));
  textarea.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      createItem(tr);
    }
  });

  statusCell.append(textarea);
  tr.append(itemCell, statusCell);
  return tr;
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

function render(focusItemId) {
  itemsEl.replaceChildren(buildNewRow(), ...items.map(buildRow));
  if (focusItemId) {
    itemsEl.querySelector(`tr[data-id="${focusItemId}"] .status-window`)?.focus();
  }
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

loadItems();
