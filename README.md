# todo.aolabs.io

Archived May 25, 2026. The Railway deployment is stopped while the project, source, and persistent volume are preserved for a possible later restore.

Password-gated two-column todo table.

Shape:

- one row per item
- `item` column for the name
- `status` column with a fixed-height scroll window that stays at the newest text while typing

Runtime:

- `GET /` serves the gated app.
- `POST /api/auth` checks `TODO_PASSWORD`, default `031120`, and sets a remembered-device cookie.
- `GET /api/todo/items` returns the raw list for authenticated users.
- `POST /api/todo/items` adds an item.
- `PATCH /api/todo/items/{item_id}` edits item name or status.
- `DELETE /api/todo/items/{item_id}` removes an item.
- `GET /api/todo/summary` is public-safe JSON for Progress.
