# todo.aolabs.io

Meeting-transcript todo extractor.

Shape:

- paste or upload a long meeting transcript
- AI extracts only transcript-supported todo rows
- rows stay editable and reviewable
- table columns: todo/context, date added, time estimate, ease /100, Disney /100, total, why
- total score is computed as `easeScore + disneyScore`
- raw transcript text is stored server-side and is not exposed through public read endpoints
- older archived Todo rows are preserved under `legacyItems` and hidden from the new transcript table

Correctness guardrails:

- no deterministic fallback extraction when AI is not configured
- every AI row includes source speaker, evidence quote, confidence, and review state
- evidence quotes are checked against the transcript chunk
- unsupported evidence lowers confidence and marks the row `needs_evidence`

Runtime:

- `GET /` serves the app.
- `GET /health` and `GET /api/health` expose app/AI readiness without secrets.
- `GET /api/todo/items` returns rows and transcript summaries.
- `POST /api/todo/transcripts/analyze` stores a transcript and appends AI-extracted rows.
- `POST /api/todo/items` adds a manual row.
- `PATCH /api/todo/items/{item_id}` edits row fields and review state.
- `DELETE /api/todo/items/{item_id}` removes a row.
- `GET /api/todo/summary` is public-safe JSON for Progress.

Railway variables:

- `OPENAI_API_KEY`
- `TODO_OPENAI_MODEL` or `OPENAI_MODEL`, default `gpt-5-mini`
- `TODO_OPENAI_REASONING_EFFORT`, default `medium`
- `TODO_STATE_PATH`, optional; Railway volume should normally use `/data/todo_state.json`
