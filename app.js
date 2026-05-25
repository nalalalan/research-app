const itemsEl = document.querySelector("#items");
const saveTimers = new Map();

let items = [];
let draftOpen = false;
let openMenuId = "";
let confirmDeleteId = "";
let draggedItemId = "";

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

function syncStatusState(textarea) {
  textarea.classList.toggle("is-empty", !textarea.value.trim());
}

function clearDragClasses() {
  document.querySelectorAll(".is-dragging, .is-drop-before, .is-drop-after").forEach((row) => {
    row.classList.remove("is-dragging", "is-drop-before", "is-drop-after");
  });
}

function placeDropMarker(row, event) {
  if (!draggedItemId || row.dataset.id === draggedItemId) return "before";
  const rect = row.getBoundingClientRect();
  const placement = event.clientY > rect.top + rect.height / 2 ? "after" : "before";
  document.querySelectorAll(".is-drop-before, .is-drop-after").forEach((entry) => {
    if (entry !== row) entry.classList.remove("is-drop-before", "is-drop-after");
  });
  row.classList.toggle("is-drop-before", placement === "before");
  row.classList.toggle("is-drop-after", placement === "after");
  return placement;
}

function moveItem(dragId, targetId, placement = "before") {
  if (!dragId || !targetId || dragId === targetId) return false;
  const fromIndex = items.findIndex((item) => item.id === dragId);
  if (fromIndex === -1) return false;
  const [draggedItem] = items.splice(fromIndex, 1);
  const targetIndex = items.findIndex((item) => item.id === targetId);
  if (targetIndex === -1) {
    items.splice(fromIndex, 0, draggedItem);
    return false;
  }
  const insertIndex = placement === "after" ? targetIndex + 1 : targetIndex;
  items.splice(insertIndex, 0, draggedItem);
  return true;
}

function moveItemToEnd(dragId) {
  const fromIndex = items.findIndex((item) => item.id === dragId);
  if (fromIndex === -1 || fromIndex === items.length - 1) return false;
  const [draggedItem] = items.splice(fromIndex, 1);
  items.push(draggedItem);
  return true;
}

async function persistOrder() {
  const response = await fetch("/api/todo/items/order", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: items.map((item) => item.id) }),
  });
  if (response.ok) {
    const payload = await response.json();
    items = payload.items || items;
  } else {
    await loadItems();
  }
}

function attachDropTarget(row, itemId) {
  row.addEventListener("dragover", (event) => {
    if (!draggedItemId || draggedItemId === itemId) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    placeDropMarker(row, event);
  });

  row.addEventListener("dragleave", () => {
    row.classList.remove("is-drop-before", "is-drop-after");
  });

  row.addEventListener("drop", async (event) => {
    if (!draggedItemId || draggedItemId === itemId) return;
    event.preventDefault();
    const placement = placeDropMarker(row, event);
    const moved = moveItem(draggedItemId, itemId, placement);
    draggedItemId = "";
    clearDragClasses();
    if (moved) await persistOrder();
    render();
  });
}

function attachEndDropTarget(row) {
  row.addEventListener("dragover", (event) => {
    if (!draggedItemId) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    document.querySelectorAll(".is-drop-before, .is-drop-after").forEach((entry) => {
      if (entry !== row) entry.classList.remove("is-drop-before", "is-drop-after");
    });
    row.classList.add("is-drop-before");
  });

  row.addEventListener("dragleave", () => {
    row.classList.remove("is-drop-before");
  });

  row.addEventListener("drop", async (event) => {
    if (!draggedItemId) return;
    event.preventDefault();
    const moved = moveItemToEnd(draggedItemId);
    draggedItemId = "";
    clearDragClasses();
    if (moved) await persistOrder();
    render();
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
  attachEndDropTarget(tr);

  const itemCell = document.createElement("td");
  itemCell.className = "item-cell";

  const itemWrap = document.createElement("div");
  itemWrap.className = "item-wrap";

  const dragSpacer = document.createElement("span");
  dragSpacer.className = "drag-spacer";
  dragSpacer.setAttribute("aria-hidden", "true");

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

  itemWrap.append(dragSpacer, itemInput, addButton);
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
  attachEndDropTarget(tr);

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

function buildDragHandle(item, row) {
  const handle = document.createElement("button");
  handle.className = "drag-handle";
  handle.type = "button";
  handle.draggable = true;
  handle.setAttribute("aria-label", "drag row");

  const dots = document.createElement("span");
  dots.className = "drag-dots";
  dots.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 6; index += 1) {
    dots.append(document.createElement("span"));
  }
  handle.append(dots);

  handle.addEventListener("dragstart", (event) => {
    draggedItemId = item.id;
    row.classList.add("is-dragging");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", item.id);
    }
  });

  handle.addEventListener("dragend", () => {
    draggedItemId = "";
    clearDragClasses();
  });

  return handle;
}

function buildRow(item) {
  const tr = document.createElement("tr");
  tr.className = "todo-row";
  tr.dataset.id = item.id;
  attachDropTarget(tr, item.id);

  const itemCell = document.createElement("td");
  itemCell.className = "item-cell";

  const itemWrap = document.createElement("div");
  itemWrap.className = "item-wrap";

  const dragHandle = buildDragHandle(item, tr);

  const nameInput = document.createElement("input");
  nameInput.className = "item-input";
  nameInput.value = item.item || "";
  nameInput.setAttribute("aria-label", "item");
  nameInput.addEventListener("input", () => {
    scheduleSave(item.id, { item: nameInput.value });
  });

  const actionWrap = buildActions(item);

  itemWrap.append(dragHandle, nameInput, actionWrap);
  itemCell.append(itemWrap);

  const statusCell = document.createElement("td");
  statusCell.className = "status-cell";

  const textarea = document.createElement("textarea");
  textarea.className = "status-window";
  textarea.value = item.status || "";
  textarea.placeholder = "";
  syncStatusState(textarea);
  textarea.spellcheck = true;
  textarea.setAttribute("aria-label", "status");
  textarea.addEventListener("input", () => {
    syncStatusState(textarea);
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
