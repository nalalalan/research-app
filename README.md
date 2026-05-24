# research.aolabs.io

Password-gated daily research ledger for Imagineer, FluxCell, and Sarrus work.

## Runtime

- `GET /` serves the gated app.
- `POST /api/auth` checks `RESEARCH_PASSWORD`, default `031120`, and sets a remembered-device cookie.
- `GET /api/research/entries` returns the raw table for authenticated users.
- `POST /api/research/entries` adds a row.
- `DELETE /api/research/entries/{entry_id}` removes a row.
- `GET /api/research/summary` is public-safe JSON for Progress and sibling app reads.

## Storage

Set `RESEARCH_STATE_PATH` for a specific file. Otherwise Railway uses `/data/research_state.json` when a volume is mounted, with `.runtime/research_state.json` as local fallback.

## Environment

- `RESEARCH_PASSWORD=031120`
- `RESEARCH_COOKIE_SECRET=<long random value>`

