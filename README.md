# todo.aolabs.io

Password-gated one-column todo list.

## Runtime

- `GET /` serves the gated app.
- `POST /api/auth` checks `TODO_PASSWORD`, default `031120`, and sets a remembered-device cookie.
- `GET /api/todo/items` returns the raw list for authenticated users.
- `POST /api/todo/items` adds an item.
- `DELETE /api/todo/items/{item_id}` removes an item.
- `GET /api/todo/summary` is public-safe JSON for Progress.

## Storage

Set `TODO_STATE_PATH` for a specific file. Otherwise Railway uses `/data/todo_state.json` when a volume is mounted, with `.runtime/todo_state.json` as local fallback.

## Environment

- `TODO_PASSWORD=031120`
- `TODO_COOKIE_SECRET=<long random value>`
