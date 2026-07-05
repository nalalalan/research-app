# todo.aolabs.io

Meeting-transcript todo extractor.

Shape:

- paste a long meeting transcript
- AI infers the transcript title/date-time from the transcript text
- AI extracts only transcript-supported todo rows; rows are added automatically after analysis
- rows stay editable and reviewable
- every row has exactly one category: `paper`, `prototype`, or `phd`; paper text/plot/figure/photo/cartoon/diagram tasks stay `paper` even when the meeting context mentions PhD, dissertation, valves, mechanisms, tests, or prototype hardware
- repeated mentions of the same todo merge into one row with additional details/evidence instead of creating duplicate rows
- high-ease rows still need enough detail to execute immediately: exact figure/section/video/object when available, the small edit/check, the visible done state, and any do-not-redo boundary from the transcript
- the todo text must render as polished sentences with punctuation between task, context, and score rationale; rows must not read like randomly appended fragments
- table filters toggle those categories and filter both active and done rows
- table columns: todo, ease /100, Disney /100, total
- table header shows active and done counts as `__ active / __ done`
- clicking the todo header cell sorts by date added, newest-to-oldest first and then oldest-to-newest; numeric columns sort high-to-low first, then low-to-high
- each todo cell renders the action/context/added time/time estimate followed by a quote block formatted as `Speaker: exact quote`
- row state is not a dropdown; the row has a direct `done` action that moves the row into a dedicated done section below the active todo table
- total score is computed as `easeScore + disneyScore`
- raw transcript text is stored server-side and exposed through readable generated PDFs under the transcription archive
- transcript cards show a real AI summary or todo-derived summary, not the metadata evidence quote
- transcript cards have a delete action that removes both the saved transcription and todo rows generated from it
- failed transcript cards have a retry action so Alan does not have to paste the same text again
- older archived Todo rows are preserved under `legacyItems` and hidden from the new transcript table

Correctness guardrails:

- no deterministic fallback extraction when AI is not configured
- every AI row includes source speaker, evidence quote, confidence, and review state
- evidence quotes are checked against the transcript chunk
- unsupported evidence lowers confidence and marks the row `needs_evidence`
- AI must not generate a question list for Alan; ambiguous discussion without a concrete next step is skipped
- interrupted in-flight analysis records are marked failed on restart instead of staying stuck as analyzing

Runtime:

- `GET /` serves the app.
- `GET /health` and `GET /api/health` expose app/AI readiness without secrets.
- `GET /api/todo/items` returns rows and transcript summaries with PDF links.
- `POST /api/todo/transcripts/analyze` stores a transcript and appends AI-extracted rows.
- `POST /api/todo/transcripts/{transcript_id}/retry` retries analysis for a saved failed transcript.
- `POST /api/todo/transcripts/{transcript_id}/rescore` recalibrates existing row scores against the current scoring rubric.
- `PATCH /api/todo/items/{item_id}` edits row fields and review state.
- `DELETE /api/todo/items/{item_id}` removes a row.
- `DELETE /api/todo/transcripts/{transcript_id}` removes a saved transcript and its generated rows.
- `GET /api/todo/transcripts/{transcript_id}/pdf` serves the formatted transcript PDF.
- `GET /api/todo/summary` is public-safe JSON for Progress.

Railway variables:

- `OPENAI_API_KEY`
- `TODO_OPENAI_MODEL` or `OPENAI_MODEL`, default `gpt-5.5`
- `TODO_OPENAI_REASONING_EFFORT`, default `medium`
- `TODO_SCORING_REASONING_EFFORT`, default `high`
- `TODO_CHUNK_CHARS`, default `18000`
- `TODO_CHUNK_OVERLAP_CHARS`, default `700`
- `TODO_ANALYSIS_MAX_OUTPUT_TOKENS`, default `32000`
- `TODO_STATE_PATH`, optional; Railway volume should normally use `/data/todo_state.json`
