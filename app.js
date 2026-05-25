const itemsEl = document.querySelector("#items");
const saveTimers = new Map();

let items = [];
let draftOpen = false;
let openMenuId = "";
let confirmDeleteId = "";

document.addEventListener("click", (event) => {
  if (!openMenuId || event.target.closest(".item-actions")) return;
  openMenuId = "";
  confirmDeleteId = "";
  render();
});

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
  draftOpen = false;
  render({ focusNewButton: true });
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

function buildNewRowButton() {
  const tr = document.createElement("tr");
  tr.className = "new-row-button-row";

  const itemCell = document.createElement("td");
  itemCell.className = "new-row-cell item-cell";
  itemCell.colSpan = 2;

  const button = document.createElement("button");
  button.className = "new-row-button";
  button.type = "button";
  button.setAttribute("aria-label", "new row");

  const plus = document.createElement("span");
  plus.className = "new-row-plus";
  plus.textContent = "+";
  plus.setAttribute("aria-hidden", "true");

  const label = document.createElement("span");
  label.textContent = "New row";

  button.append(plus, label);
  button.addEventListener("click", () => {
    draftOpen = true;
    render({ focusNew: true });
  });

  itemCell.append(button);
  tr.append(itemCell);
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

  const actionWrap = buildActions(item);

  itemWrap.append(nameInput, actionWrap);
  itemCell.append(itemWrap);

  const statusCell = document.createElement("td");
  statusCell.className = "status-cell";

  const textarea = document.createElement("textarea");
  textarea.className = "status-window";
  textarea.value = item.status || "";
  textarea.placeholder = "";
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

function buildActions(item) {
  const actionWrap = document.createElement("div");
  actionWrap.className = "item-actions";

  const menuButton = document.createElement("button");
  menuButton.className = "menu-button";
  menuButton.type = "button";
  menuButton.setAttribute("aria-label", "row actions");
  menuButton.setAttribute("aria-expanded", String(openMenuId === item.id));

  const dotStack = document.createElement("span");
  dotStack.className = "dot-stack";
  dotStack.setAttribute("aria-hidden", "true");
  dotStack.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
  menuButton.append(dotStack);
  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const shouldOpen = openMenuId !== item.id;
    openMenuId = shouldOpen ? item.id : "";
    confirmDeleteId = "";
    render();
  });

  actionWrap.append(menuButton);

  if (openMenuId !== item.id) return actionWrap;

  const menu = document.createElement("div");
  menu.className = "row-menu";
  menu.addEventListener("click", (event) => event.stopPropagation());

  if (confirmDeleteId === item.id) {
    const message = document.createElement("p");
    message.className = "confirm-text";
    message.textContent = "Are you sure you want to delete?";

    const confirmActions = document.createElement("div");
    confirmActions.className = "confirm-actions";

    const cancelButton = document.createElement("button");
    cancelButton.className = "cancel-delete";
    cancelButton.type = "button";
    cancelButton.textContent = "Cancel";
    cancelButton.addEventListener("click", () => {
      confirmDeleteId = "";
      render();
    });

    const confirmButton = document.createElement("button");
    confirmButton.className = "confirm-delete";
    confirmButton.type = "button";
    confirmButton.textContent = "Delete";
    confirmButton.addEventListener("click", async () => {
      const response = await fetch(`/api/todo/items/${item.id}`, { method: "DELETE" });
      if (response.ok) {
        items = items.filter((entry) => entry.id !== item.id);
        openMenuId = "";
        confirmDeleteId = "";
        render();
      }
    });

    confirmActions.append(cancelButton, confirmButton);
    menu.append(message, confirmActions);
  } else {
    const deleteButton = document.createElement("button");
    deleteButton.className = "menu-delete";
    deleteButton.type = "button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => {
      confirmDeleteId = item.id;
      render();
    });
    menu.append(deleteButton);
  }

  actionWrap.append(menu);
  return actionWrap;
}

function render(options = {}) {
  const rows = items.map(buildRow);
  rows.push(draftOpen ? buildNewRow() : buildNewRowButton());
  itemsEl.replaceChildren(...rows);
  if (options.focusNew) {
    itemsEl.querySelector(".new-item-input")?.focus();
  }
  if (options.focusNewButton) {
    itemsEl.querySelector(".new-row-button")?.focus();
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
