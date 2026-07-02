from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
OPENAI_API_KEY = os.getenv("TODO_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
OPENAI_MODEL = os.getenv("TODO_OPENAI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5-mini"
OPENAI_REASONING_EFFORT = os.getenv("TODO_OPENAI_REASONING_EFFORT", "medium").strip() or "medium"
MAX_TRANSCRIPT_CHARS = int(os.getenv("TODO_MAX_TRANSCRIPT_CHARS", "240000"))
CHUNK_CHARS = int(os.getenv("TODO_CHUNK_CHARS", "28000"))
CHUNK_OVERLAP_CHARS = int(os.getenv("TODO_CHUNK_OVERLAP_CHARS", "900"))
MAX_ITEMS_PER_CHUNK = int(os.getenv("TODO_MAX_ITEMS_PER_CHUNK", "18"))
STATE_SCHEMA = "transcript_todo_v1"


def _state_path() -> Path:
    configured = os.getenv("TODO_STATE_PATH", "").strip()
    if configured:
        return Path(configured)
    if Path("/data").exists():
        return Path("/data/todo_state.json")
    return ROOT_DIR / ".runtime" / "todo_state.json"


STATE_PATH = _state_path()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _default_state() -> dict[str, Any]:
    return {"schema": STATE_SCHEMA, "items": [], "transcripts": [], "legacyItems": [], "updatedAt": _now()}


def _is_transcript_state(data: dict[str, Any]) -> bool:
    if data.get("schema") == STATE_SCHEMA:
        return True
    if isinstance(data.get("transcripts"), list) and data["transcripts"]:
        return True
    items = data.get("items")
    if not isinstance(items, list):
        return False
    transcript_keys = {"task", "details", "easeScore", "disneyScore", "sourceSpeaker", "evidenceQuote", "transcriptId"}
    return any(isinstance(item, dict) and bool(transcript_keys.intersection(item.keys())) for item in items)


def _migrate_legacy_state(data: dict[str, Any]) -> dict[str, Any]:
    legacy_items = data.get("items") if isinstance(data.get("items"), list) else []
    return {
        "schema": STATE_SCHEMA,
        "items": [],
        "transcripts": [],
        "legacyItems": legacy_items,
        "migratedAt": _now(),
        "updatedAt": data.get("updatedAt") or _now(),
    }


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    if not _is_transcript_state(data):
        return _migrate_legacy_state(data)
    data["schema"] = STATE_SCHEMA
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if not isinstance(data.get("transcripts"), list):
        data["transcripts"] = []
    if not isinstance(data.get("legacyItems"), list):
        data["legacyItems"] = []
    data.setdefault("updatedAt", _now())
    return data


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["schema"] = STATE_SCHEMA
    state.setdefault("legacyItems", [])
    state["updatedAt"] = _now()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _safe_text(value: Any, limit: int = 5000) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").strip()
    if len(text) > limit:
        return text[:limit].rstrip()
    return text


def _score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 0


def _total_score(item: dict[str, Any]) -> int:
    return _score(item.get("easeScore")) + _score(item.get("disneyScore", item.get("impactScore")))


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _quote_verified(transcript: str, quote: str) -> bool:
    normalized_quote = _normalize_for_match(quote)
    if len(normalized_quote) < 12:
        return False
    return normalized_quote in _normalize_for_match(transcript)


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    legacy_task = item.get("task", item.get("item", ""))
    legacy_details = item.get("details", item.get("status", ""))
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    created = str(item.get("createdAt", ""))
    return {
        "id": str(item.get("id", "")),
        "task": _safe_text(legacy_task, 800),
        "details": _safe_text(legacy_details, 6000),
        "dateAdded": _safe_text(item.get("dateAdded") or (created[:10] if created else ""), 40),
        "timeEstimate": _safe_text(item.get("timeEstimate"), 80),
        "easeScore": _score(item.get("easeScore")),
        "disneyScore": _score(item.get("disneyScore", item.get("impactScore"))),
        "totalScore": _total_score(item),
        "why": _safe_text(item.get("why", item.get("impactWhy")), 1200),
        "sourceTranscriptId": _safe_text(item.get("sourceTranscriptId"), 60),
        "sourceSpeaker": _safe_text(item.get("sourceSpeaker"), 120),
        "evidence": [_safe_text(entry, 500) for entry in evidence[:3]],
        "confidence": _safe_text(item.get("confidence") or "manual", 24),
        "state": _safe_text(item.get("state") or "active", 30),
        "openQuestions": [_safe_text(entry, 220) for entry in (item.get("openQuestions") or [])[:3]],
        "createdAt": str(item.get("createdAt", "")),
        "updatedAt": str(item.get("updatedAt", "")),
    }


def _compact_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": str(transcript.get("id", "")),
        "name": _safe_text(transcript.get("name"), 180),
        "createdAt": str(transcript.get("createdAt", "")),
        "characterCount": int(transcript.get("characterCount") or 0),
        "chunkCount": int(transcript.get("chunkCount") or 0),
        "itemCount": int(transcript.get("itemCount") or 0),
        "model": _safe_text(transcript.get("model"), 80),
        "status": _safe_text(transcript.get("status"), 40),
        "error": _safe_text(transcript.get("error"), 400),
    }
    return payload


def _chunk_transcript(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        rough_end = min(len(text), start + CHUNK_CHARS)
        end = rough_end
        if rough_end < len(text):
            window = text[start:rough_end]
            split_at = max(window.rfind("\n\n"), window.rfind("\n"))
            if split_at > int(CHUNK_CHARS * 0.55):
                end = start + split_at
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(0, end - CHUNK_OVERLAP_CHARS)
    return chunks


def _extract_response_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    for item in data.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    return ""


def _candidate_key(candidate: dict[str, Any]) -> str:
    task = re.sub(r"[^a-z0-9]+", " ", str(candidate.get("task", "")).lower()).strip()
    speaker = re.sub(r"[^a-z0-9]+", " ", str(candidate.get("sourceSpeaker", "")).lower()).strip()
    return f"{speaker}|{task[:140]}"


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for candidate in candidates:
        key = _candidate_key(candidate)
        if not key.strip("|"):
            key = secrets.token_hex(8)
        if key not in seen:
            seen[key] = candidate
            order.append(key)
            continue
        existing = seen[key]
        existing["easeScore"] = max(_score(existing.get("easeScore")), _score(candidate.get("easeScore")))
        existing["disneyScore"] = max(_score(existing.get("disneyScore")), _score(candidate.get("disneyScore")))
        for quote in candidate.get("evidence", []):
            if quote and quote not in existing["evidence"] and len(existing["evidence"]) < 3:
                existing["evidence"].append(quote)
        if candidate.get("details") and candidate["details"] not in existing["details"]:
            existing["details"] = f"{existing['details']} {candidate['details']}".strip()
        for question in candidate.get("openQuestions", []):
            if question not in existing["openQuestions"] and len(existing["openQuestions"]) < 3:
                existing["openQuestions"].append(question)
    return [seen[key] for key in order]


async def _analyze_chunk(transcript_name: str, chunk: str, chunk_index: int, chunk_count: int) -> list[dict[str, Any]]:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="AI analysis is not configured")

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "maxItems": MAX_ITEMS_PER_CHUNK,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "task",
                        "details",
                        "sourceSpeaker",
                        "timeEstimate",
                        "easeScore",
                        "disneyScore",
                        "why",
                        "evidenceQuote",
                        "confidence",
                        "openQuestions",
                    ],
                    "properties": {
                        "task": {"type": "string"},
                        "details": {"type": "string"},
                        "sourceSpeaker": {"type": "string"},
                        "timeEstimate": {"type": "string"},
                        "easeScore": {"type": "integer", "minimum": 0, "maximum": 100},
                        "disneyScore": {"type": "integer", "minimum": 0, "maximum": 100},
                        "why": {"type": "string"},
                        "evidenceQuote": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "openQuestions": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                    },
                },
            }
        },
    }

    input_text = "\n".join(
        [
            f"Transcript name: {transcript_name or 'untitled meeting'}",
            f"Chunk: {chunk_index + 1} of {chunk_count}",
            "",
            "Transcript chunk:",
            chunk,
        ]
    )

    request_body = {
        "model": OPENAI_MODEL,
        "store": False,
        "reasoning": {"effort": OPENAI_REASONING_EFFORT},
        "max_output_tokens": 5200,
        "instructions": "\n".join(
            [
                "You convert a private meeting transcript into a high-stakes todo table for Alan.",
                "Extract only concrete action items that are supported by the transcript text.",
                "Never invent a task, owner, speaker, date, priority, score, or context that is not supported by the transcript.",
                "If the transcript says a topic was discussed but no action is implied, do not create a todo row.",
                "If an action is ambiguous, create a row only when there is a real next step and put the uncertainty in openQuestions.",
                "Dependent follow-up checks still count as todo rows when someone offers or requests them, such as checking a PDF after an edit, verifying a figure after export, or reviewing a citation after insertion. Mark the dependency in details or openQuestions instead of dropping the row.",
                "Use the speaker names in the transcript when they matter. sourceSpeaker should be the person who assigned, requested, volunteered, or clarified the action. Leave it blank only when the transcript has no speaker names.",
                "task is the thing to be done, written as a direct concrete action.",
                "details must include specific context: who said what, what was decided, and what source condition matters.",
                "timeEstimate is a practical estimate such as 10 min, 30 min, 2 hr, half day, 1 day, or unknown.",
                "easeScore is 0-100 for how easy this is to finish quickly. Very easy immediate tasks should score high. Long, ambiguous, blocked, or emotionally heavy tasks should score lower.",
                "disneyScore is 0-100 for future-goal value, named after Alan's Disney/Imagineering goal but broader than literal Disney wording. Treat paper progress, research progress, mechanism/simulator progress, portfolio evidence, career positioning, life stability, goals, dreams, and current physical-system work as direct Disney-score evidence when the transcript supports that lane. Do not require the word Disney to appear for a paper or research task to score high. Do not give a negligible Disney score to paper, PDF, citation, figure, or research-support work merely because it is editing or checking; score minor polish moderate, claim/evidence/career-facing work high, and direct portfolio/research breakthroughs highest.",
                "why must explain both scores in one short source-grounded note. Do not write motivational copy.",
                "evidenceQuote must be an exact continuous quote copied from the transcript chunk, 12-260 characters, that supports the row.",
                "confidence is high only when the transcript clearly supports the task, details, ease basis, and Disney-goal basis. Use medium or low for ambiguous ownership, missing date, weak ease basis, or weak Disney basis.",
                "Return JSON only through the schema. Keep strings compact. Do not include markdown.",
            ]
        ),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": input_text}]}],
        "text": {"format": {"type": "json_schema", "name": "todo_transcript_items", "strict": True, "schema": schema}},
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(170.0, connect=20.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=request_body,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="AI analysis timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="AI analysis request failed") from exc

    if response.status_code >= 400:
        error_text = response.text[:500]
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {error_text}")

    text = _extract_response_text(response.json())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI analysis returned invalid JSON") from exc

    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return []

    candidates: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        quote = _safe_text(raw.get("evidenceQuote"), 500)
        verified = _quote_verified(chunk, quote)
        confidence = _safe_text(raw.get("confidence") or "low", 20)
        if not verified:
            confidence = "low"
        candidates.append(
            {
                "task": _safe_text(raw.get("task"), 800),
                "details": _safe_text(raw.get("details"), 6000),
                "sourceSpeaker": _safe_text(raw.get("sourceSpeaker"), 120),
                "timeEstimate": _safe_text(raw.get("timeEstimate"), 80),
                "easeScore": _score(raw.get("easeScore")),
                "disneyScore": _score(raw.get("disneyScore")),
                "why": _safe_text(raw.get("why"), 1200),
                "evidence": [quote] if quote else [],
                "confidence": confidence,
                "state": "needs_evidence" if quote and not verified else "review",
                "openQuestions": [_safe_text(entry, 220) for entry in (raw.get("openQuestions") or [])[:3]],
            }
        )
    return candidates


async def _analyze_transcript(transcript_id: str, name: str, text: str) -> list[dict[str, Any]]:
    chunks = _chunk_transcript(text)
    all_candidates: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        chunk_candidates = await _analyze_chunk(name, chunk, index, len(chunks))
        all_candidates.extend(chunk_candidates)
    candidates = _dedupe_candidates(all_candidates)
    now = _now()
    return [
        {
            "id": secrets.token_hex(8),
            "task": candidate["task"],
            "details": candidate["details"],
            "dateAdded": _today(),
            "timeEstimate": candidate["timeEstimate"],
            "easeScore": candidate["easeScore"],
            "disneyScore": candidate["disneyScore"],
            "why": candidate["why"],
            "sourceTranscriptId": transcript_id,
            "sourceSpeaker": candidate["sourceSpeaker"],
            "evidence": candidate["evidence"],
            "confidence": candidate["confidence"],
            "state": candidate["state"],
            "openQuestions": candidate["openQuestions"],
            "createdAt": now,
            "updatedAt": now,
        }
        for candidate in candidates
        if candidate.get("task")
    ]


class AnalyzeTranscriptBody(BaseModel):
    name: str = Field(default="", max_length=180)
    transcript: str = Field(min_length=1, max_length=300000)


class CreateItemBody(BaseModel):
    task: str = Field(min_length=1, max_length=800)
    details: str = Field(default="", max_length=6000)
    dateAdded: str = Field(default="", max_length=40)
    timeEstimate: str = Field(default="", max_length=80)
    easeScore: int = Field(default=0, ge=0, le=100)
    disneyScore: int = Field(default=0, ge=0, le=100)
    why: str = Field(default="", max_length=1200)


class UpdateItemBody(BaseModel):
    task: str | None = Field(default=None, max_length=800)
    details: str | None = Field(default=None, max_length=6000)
    dateAdded: str | None = Field(default=None, max_length=40)
    timeEstimate: str | None = Field(default=None, max_length=80)
    easeScore: int | None = Field(default=None, ge=0, le=100)
    disneyScore: int | None = Field(default=None, ge=0, le=100)
    why: str | None = Field(default=None, max_length=1200)
    state: str | None = Field(default=None, max_length=30)


class ReorderItemsBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


app = FastAPI(title="AO Todo", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://aolabs.io", "https://todo.aolabs.io", "http://localhost:8783", "http://127.0.0.1:8783"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "ao-todo",
        "aiConfigured": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL if OPENAI_API_KEY else "",
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/api/todo/items")
async def list_items() -> dict[str, Any]:
    state = _load_state()
    return {
        "items": [_compact_item(item) for item in state.get("items", [])],
        "transcripts": [_compact_transcript(entry) for entry in state.get("transcripts", [])],
        "updatedAt": state.get("updatedAt"),
        "aiConfigured": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL if OPENAI_API_KEY else "",
    }


@app.post("/api/todo/transcripts/analyze")
async def analyze_transcript(body: AnalyzeTranscriptBody) -> dict[str, Any]:
    transcript_text = body.transcript.strip()
    if len(transcript_text) > MAX_TRANSCRIPT_CHARS:
        raise HTTPException(status_code=413, detail=f"Transcript limit is {MAX_TRANSCRIPT_CHARS} characters")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="AI analysis is not configured")

    state = _load_state()
    transcript_id = secrets.token_hex(8)
    transcript = {
        "id": transcript_id,
        "name": body.name.strip() or f"meeting {time.strftime('%Y-%m-%d')}",
        "text": transcript_text,
        "createdAt": _now(),
        "characterCount": len(transcript_text),
        "chunkCount": len(_chunk_transcript(transcript_text)),
        "itemCount": 0,
        "model": OPENAI_MODEL,
        "status": "analyzing",
        "error": "",
    }
    state.setdefault("transcripts", []).append(transcript)
    _save_state(state)

    try:
        new_items = await _analyze_transcript(transcript_id, transcript["name"], transcript_text)
    except HTTPException as exc:
        state = _load_state()
        for entry in state.get("transcripts", []):
            if str(entry.get("id")) == transcript_id:
                entry["status"] = "failed"
                entry["error"] = _safe_text(exc.detail, 400)
                break
        _save_state(state)
        raise

    state = _load_state()
    for entry in state.get("transcripts", []):
        if str(entry.get("id")) == transcript_id:
            entry["status"] = "complete"
            entry["itemCount"] = len(new_items)
            entry["error"] = ""
            break
    state.setdefault("items", []).extend(new_items)
    _save_state(state)
    return {
        "transcript": _compact_transcript(transcript | {"status": "complete", "itemCount": len(new_items)}),
        "items": [_compact_item(item) for item in new_items],
        "allItems": [_compact_item(item) for item in state.get("items", [])],
        "updatedAt": state["updatedAt"],
    }


@app.post("/api/todo/items")
async def create_item(body: CreateItemBody) -> dict[str, Any]:
    state = _load_state()
    now = _now()
    item = {
        "id": secrets.token_hex(8),
        "task": body.task.strip(),
        "details": body.details.strip(),
        "dateAdded": body.dateAdded.strip() or _today(),
        "timeEstimate": body.timeEstimate.strip(),
        "easeScore": _score(body.easeScore),
        "disneyScore": _score(body.disneyScore),
        "why": body.why.strip(),
        "sourceTranscriptId": "",
        "sourceSpeaker": "",
        "evidence": [],
        "confidence": "manual",
        "state": "active",
        "openQuestions": [],
        "createdAt": now,
        "updatedAt": now,
    }
    state.setdefault("items", []).append(item)
    _save_state(state)
    return {"item": _compact_item(item), "updatedAt": state["updatedAt"]}


@app.patch("/api/todo/items/order")
async def reorder_items(body: ReorderItemsBody) -> dict[str, Any]:
    state = _load_state()
    items = state.get("items", [])
    order = [str(item.get("id", "")) for item in items]
    requested = [str(item_id) for item_id in body.ids]
    if len(set(requested)) != len(requested) or set(requested) != set(order):
        raise HTTPException(status_code=400, detail="order must include each item once")
    by_id = {str(item.get("id", "")): item for item in items}
    state["items"] = [by_id[item_id] for item_id in requested]
    _save_state(state)
    return {"items": [_compact_item(item) for item in state["items"]], "updatedAt": state["updatedAt"]}


@app.patch("/api/todo/items/{item_id}")
async def update_item(item_id: str, body: UpdateItemBody) -> dict[str, Any]:
    state = _load_state()
    allowed_states = {"review", "active", "done", "set_aside", "needs_evidence", "manual"}
    for item in state.get("items", []):
        if str(item.get("id")) == item_id:
            for field in ["task", "details", "dateAdded", "timeEstimate", "why"]:
                value = getattr(body, field)
                if value is not None:
                    item[field] = value.strip()
            if body.easeScore is not None:
                item["easeScore"] = _score(body.easeScore)
            if body.disneyScore is not None:
                item["disneyScore"] = _score(body.disneyScore)
            if body.state is not None:
                item["state"] = body.state.strip() if body.state.strip() in allowed_states else "review"
            item["updatedAt"] = _now()
            _save_state(state)
            return {"item": _compact_item(item), "updatedAt": state["updatedAt"]}
    raise HTTPException(status_code=404, detail="not found")


@app.delete("/api/todo/items/{item_id}")
async def delete_item(item_id: str) -> dict[str, Any]:
    state = _load_state()
    before = len(state.get("items", []))
    state["items"] = [item for item in state.get("items", []) if str(item.get("id")) != item_id]
    if len(state["items"]) == before:
        raise HTTPException(status_code=404, detail="not found")
    _save_state(state)
    return {"status": "ok", "updatedAt": state["updatedAt"]}


@app.get("/api/todo/summary")
async def todo_summary() -> dict[str, Any]:
    state = _load_state()
    items = [_compact_item(item) for item in state.get("items", [])]
    transcripts = state.get("transcripts", [])
    by_state: dict[str, int] = {}
    for item in items:
        by_state[item["state"]] = by_state.get(item["state"], 0) + 1
    return {
        "service": "ao-todo",
        "count": len(items),
        "states": by_state,
        "transcriptCount": len(transcripts),
        "latestTranscriptAt": str(transcripts[-1].get("createdAt", "")) if transcripts else "",
        "updatedAt": state.get("updatedAt"),
        "aiConfigured": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL if OPENAI_API_KEY else "",
        "visibility": "private_rows_public_counts_only",
    }


@app.get("/{asset_path:path}")
async def static_asset(asset_path: str) -> FileResponse:
    allowed_suffixes = {".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".webmanifest", ".html"}
    path = (ROOT_DIR / asset_path).resolve()
    if ROOT_DIR not in path.parents and path != ROOT_DIR:
        raise HTTPException(status_code=404, detail="not found")
    if path.exists() and path.is_file() and path.suffix.lower() in allowed_suffixes:
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="not found")
