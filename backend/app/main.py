from __future__ import annotations

import io
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
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


ROOT_DIR = Path(__file__).resolve().parents[2]
OPENAI_API_KEY = os.getenv("TODO_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
OPENAI_MODEL = os.getenv("TODO_OPENAI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5-mini"
OPENAI_REASONING_EFFORT = os.getenv("TODO_OPENAI_REASONING_EFFORT", "medium").strip() or "medium"
MAX_TRANSCRIPT_CHARS = int(os.getenv("TODO_MAX_TRANSCRIPT_CHARS", "240000"))
CHUNK_CHARS = int(os.getenv("TODO_CHUNK_CHARS", "28000"))
CHUNK_OVERLAP_CHARS = int(os.getenv("TODO_CHUNK_OVERLAP_CHARS", "900"))
MAX_ITEMS_PER_CHUNK = int(os.getenv("TODO_MAX_ITEMS_PER_CHUNK", "18"))
ANALYSIS_MAX_OUTPUT_TOKENS = int(os.getenv("TODO_ANALYSIS_MAX_OUTPUT_TOKENS", "12000"))
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
        "meetingDateTime": _safe_text(transcript.get("meetingDateTime"), 120),
        "metadataBasis": _safe_text(transcript.get("metadataBasis"), 400),
        "createdAt": str(transcript.get("createdAt", "")),
        "characterCount": int(transcript.get("characterCount") or 0),
        "chunkCount": int(transcript.get("chunkCount") or 0),
        "itemCount": int(transcript.get("itemCount") or 0),
        "model": _safe_text(transcript.get("model"), 80),
        "status": _safe_text(transcript.get("status"), 40),
        "error": _safe_text(transcript.get("error"), 400),
        "pdfUrl": f"/api/todo/transcripts/{transcript.get('id', '')}/pdf",
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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "transcript"


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


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = _safe_text(text, 1_000_000).strip()
    if not raw:
        raise ValueError("empty response")
    candidates = [raw]
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(raw[first : last + 1].strip())
    decoder = json.JSONDecoder()
    errors: list[str] = []
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            try:
                payload, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError as raw_exc:
                errors.append(str(raw_exc))
                continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("; ".join(errors) or "not a JSON object")


async def _infer_transcript_metadata(text: str) -> dict[str, str]:
    excerpt = text[:16000]
    fallback = {
        "title": f"meeting {time.strftime('%Y-%m-%d')}",
        "dateTime": "date not stated",
        "basis": "",
        "confidence": "low",
    }
    if not OPENAI_API_KEY:
        return fallback

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "dateTime", "basis", "confidence"],
        "properties": {
            "title": {"type": "string"},
            "dateTime": {"type": "string"},
            "basis": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
    }
    request_body = {
        "model": OPENAI_MODEL,
        "store": False,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 900,
        "instructions": "\n".join(
            [
                "Infer compact metadata for a meeting transcript from the transcript text only.",
                "Do not use filename, upload time, or outside knowledge.",
                "title should be a short readable meeting title, 3-9 words, based on the actual topic.",
                "dateTime should be the meeting date/time if explicitly stated or strongly implied by the transcript. If not stated, write 'date not stated'.",
                "basis must be an exact short quote or phrase from the transcript that supports the title/date. If no date is stated, basis may support only the title.",
                "confidence is high only when both title and date/time are clearly supported. Use medium for a clear topic but unstated date. Use low for weak topic evidence.",
                "Return JSON only through the schema.",
            ]
        ),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": excerpt}]}],
        "text": {"format": {"type": "json_schema", "name": "todo_transcript_metadata", "strict": True, "schema": schema}},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=request_body,
            )
    except httpx.HTTPError:
        return fallback
    if response.status_code >= 400:
        return fallback
    try:
        payload = _parse_json_object(_extract_response_text(response.json()))
    except Exception:
        return fallback
    title = _safe_text(payload.get("title"), 180) or fallback["title"]
    date_time = _safe_text(payload.get("dateTime"), 120) or "date not stated"
    basis = _safe_text(payload.get("basis"), 400)
    confidence = _safe_text(payload.get("confidence"), 20) or "low"
    return {"title": title, "dateTime": date_time, "basis": basis, "confidence": confidence}


def _split_long_text(text: str, size: int = 1400) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while len(remaining) > size:
        split_at = max(remaining.rfind(". ", 0, size), remaining.rfind(" ", 0, size))
        if split_at < int(size * 0.55):
            split_at = size
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _transcript_story_blocks(text: str, styles: dict[str, ParagraphStyle]) -> list[Any]:
    story: list[Any] = []
    speaker_pattern = re.compile(r"^([A-Za-z][A-Za-z0-9 ._'\-]{0,54}):\s*(.*)$")
    timestamp_pattern = re.compile(r"^(\[?\d{1,2}:\d{2}(?::\d{2})?\]?|\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))\s+(.+)$")
    for raw_block in re.split(r"\n{2,}", text.strip()):
        block = raw_block.strip()
        if not block:
            continue
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            speaker = speaker_pattern.match(line)
            timestamp = timestamp_pattern.match(line)
            if speaker:
                name = speaker.group(1).strip()
                spoken = speaker.group(2).strip()
                for index, part in enumerate(_split_long_text(spoken or "")):
                    prefix = f"<b>{name}</b> " if index == 0 else ""
                    story.append(Paragraph(prefix + _xml_escape(part), styles["transcript"]))
                    story.append(Spacer(1, 0.04 * inch))
            elif timestamp:
                stamp = timestamp.group(1).strip()
                spoken = timestamp.group(2).strip()
                story.append(Paragraph(f"<b>{_xml_escape(stamp)}</b> {_xml_escape(spoken)}", styles["transcript"]))
                story.append(Spacer(1, 0.04 * inch))
            else:
                for part in _split_long_text(line):
                    story.append(Paragraph(_xml_escape(part), styles["transcript"]))
                    story.append(Spacer(1, 0.04 * inch))
        story.append(Spacer(1, 0.08 * inch))
    return story


def _xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_transcript_pdf(transcript: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.72 * inch,
        rightMargin=0.72 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.68 * inch,
        title=_safe_text(transcript.get("name"), 180) or "transcript",
    )
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "TodoTranscriptTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#1d2322"),
            spaceAfter=8,
        ),
        "meta": ParagraphStyle(
            "TodoTranscriptMeta",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#66706e"),
            spaceAfter=10,
        ),
        "transcript": ParagraphStyle(
            "TodoTranscriptBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10.2,
            leading=14.5,
            textColor=colors.HexColor("#202827"),
            spaceAfter=0,
        ),
    }
    title = _safe_text(transcript.get("name"), 180) or "transcript"
    meeting_time = _safe_text(transcript.get("meetingDateTime"), 120) or "date not stated"
    created = _safe_text(transcript.get("createdAt"), 80)
    basis = _safe_text(transcript.get("metadataBasis"), 400)
    meta_parts = [meeting_time, f"{int(transcript.get('characterCount') or 0):,} characters"]
    if created:
        meta_parts.append(f"uploaded {created}")
    story: list[Any] = [
        Paragraph(_xml_escape(title), styles["title"]),
        Paragraph(_xml_escape(" / ".join(meta_parts)), styles["meta"]),
    ]
    if basis:
        story.extend([Paragraph(_xml_escape(basis), styles["meta"]), Spacer(1, 0.08 * inch)])
    story.extend(_transcript_story_blocks(str(transcript.get("text", "")), styles))
    doc.build(story)
    return buffer.getvalue()



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
        "max_output_tokens": ANALYSIS_MAX_OUTPUT_TOKENS,
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

    response_payload = response.json()
    text = _extract_response_text(response_payload)
    try:
        payload = _parse_json_object(text)
    except ValueError as exc:
        if response_payload.get("status") == "incomplete":
            raise HTTPException(status_code=502, detail="AI response was incomplete; the transcription was saved") from exc
        raise HTTPException(status_code=502, detail="AI response could not be read; the transcription was saved") from exc

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
    transcript: str = Field(min_length=1, max_length=300000)


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
    metadata = await _infer_transcript_metadata(transcript_text)
    transcript = {
        "id": transcript_id,
        "name": metadata["title"],
        "meetingDateTime": metadata["dateTime"],
        "metadataBasis": metadata["basis"],
        "metadataConfidence": metadata["confidence"],
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
        "allTranscripts": [_compact_transcript(entry) for entry in state.get("transcripts", [])],
        "updatedAt": state["updatedAt"],
    }


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


@app.delete("/api/todo/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: str) -> dict[str, Any]:
    state = _load_state()
    transcripts = state.get("transcripts", [])
    before_transcripts = len(transcripts)
    state["transcripts"] = [entry for entry in transcripts if str(entry.get("id")) != transcript_id]
    if len(state["transcripts"]) == before_transcripts:
        raise HTTPException(status_code=404, detail="not found")
    before_items = len(state.get("items", []))
    state["items"] = [
        item for item in state.get("items", []) if str(item.get("sourceTranscriptId")) != transcript_id
    ]
    removed_items = before_items - len(state["items"])
    _save_state(state)
    return {
        "status": "ok",
        "removedItems": removed_items,
        "items": [_compact_item(item) for item in state.get("items", [])],
        "transcripts": [_compact_transcript(entry) for entry in state.get("transcripts", [])],
        "updatedAt": state["updatedAt"],
    }


@app.get("/api/todo/transcripts/{transcript_id}/pdf")
async def transcript_pdf(transcript_id: str) -> Response:
    state = _load_state()
    for transcript in state.get("transcripts", []):
        if str(transcript.get("id")) == transcript_id:
            pdf = _render_transcript_pdf(transcript)
            filename = f"{_slug(_safe_text(transcript.get('name'), 120))}.pdf"
            return Response(
                pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="{filename}"'},
            )
    raise HTTPException(status_code=404, detail="not found")


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
        "visibility": "public_rows_and_transcript_pdfs",
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
