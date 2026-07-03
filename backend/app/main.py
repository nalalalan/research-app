from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
OPENAI_MODEL = os.getenv("TODO_OPENAI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5.5"
OPENAI_REASONING_EFFORT = os.getenv("TODO_OPENAI_REASONING_EFFORT", "medium").strip() or "medium"
SCORING_REASONING_EFFORT = os.getenv("TODO_SCORING_REASONING_EFFORT", "high").strip() or "high"
MAX_TRANSCRIPT_CHARS = int(os.getenv("TODO_MAX_TRANSCRIPT_CHARS", "240000"))
CHUNK_CHARS = int(os.getenv("TODO_CHUNK_CHARS", "18000"))
CHUNK_OVERLAP_CHARS = int(os.getenv("TODO_CHUNK_OVERLAP_CHARS", "700"))
MAX_ITEMS_PER_CHUNK = int(os.getenv("TODO_MAX_ITEMS_PER_CHUNK", "18"))
MAX_SCORE_CALIBRATION_ITEMS = int(os.getenv("TODO_MAX_SCORE_CALIBRATION_ITEMS", "70"))
ANALYSIS_MAX_OUTPUT_TOKENS = int(os.getenv("TODO_ANALYSIS_MAX_OUTPUT_TOKENS", "32000"))
STALE_ANALYSIS_SECONDS = int(os.getenv("TODO_STALE_ANALYSIS_SECONDS", "45"))
STATE_SCHEMA = "transcript_todo_v1"
ACTIVE_ANALYSES: set[str] = set()


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


def _mark_interrupted_analyses() -> None:
    state = _load_state()
    changed = False
    for transcript in state.get("transcripts", []):
        if transcript.get("status") == "analyzing":
            transcript["status"] = "failed"
            transcript["error"] = "analysis was interrupted before rows were saved"
            changed = True
    if changed:
        _save_state(state)


def _seconds_since(value: Any) -> float:
    try:
        created = datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return 1_000_000.0
    return max(0.0, datetime.now(timezone.utc).timestamp() - created.timestamp())


def _mark_disconnected_analyses(state: dict[str, Any]) -> bool:
    changed = False
    for transcript in state.get("transcripts", []):
        transcript_id = str(transcript.get("id", ""))
        if transcript.get("status") != "analyzing":
            continue
        if transcript_id in ACTIVE_ANALYSES:
            continue
        if _seconds_since(transcript.get("createdAt")) < STALE_ANALYSIS_SECONDS:
            continue
        transcript["status"] = "failed"
        transcript["error"] = "analysis was interrupted before rows were saved; retry this saved transcription"
        changed = True
    return changed


def _load_state_for_read() -> dict[str, Any]:
    state = _load_state()
    if _mark_disconnected_analyses(state):
        _save_state(state)
    return state


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


TODO_CATEGORIES = {"paper", "prototype", "phd"}
PHD_KEYWORDS = ("phd", "ph.d", "proposal", "dissertation", "thesis", "committee", "defense", "qualifying")
PAPER_ARTIFACT_KEYWORDS = (
    "manuscript",
    "abstract",
    "caption",
    "citation",
    "reference",
    "references",
    "submission",
    "journal",
    "reviewer",
    "figure",
    "results",
    "discussion",
    "methods",
    "section",
    "latex",
    "overleaf",
    "pdf",
    "chi",
    "picture",
    "pictures",
    "photo",
    "photos",
    "image",
    "images",
    "cartoon",
    "diagram",
    "drawing",
    "illustration",
    "plot",
    "plots",
    "graph",
    "graphs",
    "chart",
    "charts",
    "text",
    "clarification",
    "explanation",
    "claim",
    "characterization",
    "visual",
)
PAPER_HARD_OVERRIDE_KEYWORDS = (
    "manuscript",
    "abstract",
    "caption",
    "citation",
    "reference",
    "references",
    "journal",
    "reviewer",
    "figure",
    "latex",
    "overleaf",
    "pdf",
    "chi",
    "picture",
    "pictures",
    "photo",
    "photos",
    "image",
    "images",
    "cartoon",
    "diagram",
    "drawing",
    "illustration",
    "plot",
    "plots",
    "graph",
    "graphs",
    "chart",
    "charts",
    "text",
    "clarification",
    "explanation",
    "claim",
    "characterization",
    "visual",
)
PAPER_OUTPUT_KEYWORDS = PAPER_HARD_OVERRIDE_KEYWORDS + (
    "writeup",
    "write-up",
    "video explanation",
    "result explanation",
)
PAPER_ACTION_KEYWORDS = (
    "add",
    "check",
    "describe",
    "draw",
    "edit",
    "explain",
    "clarify",
    "include",
    "insert",
    "make",
    "plot",
    "present",
    "revise",
    "review",
    "show",
    "submit",
    "update",
    "write",
)
PROTOTYPE_KEYWORDS = (
    "prototype",
    "build",
    "valve",
    "epm",
    "magnet",
    "magnetic",
    "manifold",
    "hardware",
    "cad",
    "fabricat",
    "print",
    "assembly",
    "actuator",
    "mechanism",
    "test",
    "measurement",
    "experiment",
    "comsol",
    "simulation",
)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_action_and_output(text: str) -> bool:
    return _contains_any(text, PAPER_ACTION_KEYWORDS) and _contains_any(text, PAPER_OUTPUT_KEYWORDS)


def _category_text(item: dict[str, Any]) -> tuple[str, str]:
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    task_text = _safe_text(item.get("task"), 800).lower()
    full_text = " ".join(
        [
            task_text,
            _safe_text(item.get("details"), 2000),
            _safe_text(item.get("why"), 800),
            _safe_text(item.get("quote"), 800),
            " ".join(_safe_text(entry, 500) for entry in evidence if isinstance(entry, str)),
        ]
    ).lower()
    return task_text, full_text


def _infer_todo_category(item: dict[str, Any]) -> str | None:
    task_text, text = _category_text(item)
    has_phd = _contains_any(text, PHD_KEYWORDS)
    task_is_phd_admin = _contains_any(task_text, PHD_KEYWORDS)
    has_paper_artifact = _contains_any(text, PAPER_ARTIFACT_KEYWORDS)
    has_paper_hard_override = _contains_any(text, PAPER_HARD_OVERRIDE_KEYWORDS)
    has_paper_action = _contains_any(text, PAPER_ACTION_KEYWORDS)
    has_paper_named = "paper" in text or "manuscript" in text
    task_is_paper_output = _contains_action_and_output(task_text)
    has_paper_work = (has_paper_artifact and not task_is_phd_admin) or has_paper_hard_override or (
        has_paper_named and has_paper_action and not task_is_phd_admin
    ) or task_is_paper_output
    has_prototype_work = _contains_any(text, PROTOTYPE_KEYWORDS)

    if has_paper_work:
        return "paper"
    if has_prototype_work:
        return "prototype"
    if has_phd:
        return "phd"
    return None


def _todo_category(item: dict[str, Any]) -> str:
    inferred = _infer_todo_category(item)
    if inferred:
        return inferred
    explicit = _safe_text(item.get("category"), 40).strip().lower()
    if explicit in TODO_CATEGORIES:
        return explicit
    return "phd"


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
        "category": _todo_category(item),
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
        "createdAt": str(item.get("createdAt", "")),
        "updatedAt": str(item.get("updatedAt", "")),
        "doneAt": str(item.get("doneAt", "")),
    }


def _fallback_transcript_summary(transcript: dict[str, Any], linked_items: list[dict[str, Any]] | None = None) -> str:
    title = _safe_text(transcript.get("name"), 120) or "transcription"
    linked_items = linked_items or []
    tasks = [
        _safe_text(item.get("task") or item.get("item"), 120)
        for item in sorted(linked_items, key=lambda item: _total_score(item), reverse=True)
    ]
    tasks = [task.rstrip(".") for task in tasks if task]
    if tasks:
        sample = "; ".join(tasks[:3])
        return _safe_text(f"{title}: {sample}.", 700)
    count = int(transcript.get("itemCount") or 0)
    if count:
        noun = "todo" if count == 1 else "todos"
        return f"{title}: {count} {noun} extracted from this transcription."
    if title and title != "transcription":
        return f"{title}: no supported todos extracted."
    return ""


def _compact_transcript(transcript: dict[str, Any], items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    linked_items = [
        item
        for item in (items or [])
        if str(item.get("sourceTranscriptId", "")) == str(transcript.get("id", ""))
    ]
    summary = _safe_text(transcript.get("summary") or transcript.get("metadataSummary"), 700)
    if not summary:
        summary = _fallback_transcript_summary(transcript, linked_items)
    payload = {
        "id": str(transcript.get("id", "")),
        "name": _safe_text(transcript.get("name"), 180),
        "meetingDateTime": _safe_text(transcript.get("meetingDateTime"), 120),
        "metadataBasis": _safe_text(transcript.get("metadataBasis"), 400),
        "summary": summary,
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
        "summary": "",
        "basis": "",
        "confidence": "low",
    }
    if not OPENAI_API_KEY:
        return fallback

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "dateTime", "summary", "basis", "confidence"],
        "properties": {
            "title": {"type": "string"},
            "dateTime": {"type": "string"},
            "summary": {"type": "string"},
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
                "summary should be a useful 1-2 sentence summary, 18-55 words, of what the transcript covered and why todo rows were created.",
                "summary must not be a raw quote, a filename, or a generic phrase like 'meeting transcript'.",
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
    summary = _safe_text(payload.get("summary"), 700)
    basis = _safe_text(payload.get("basis"), 400)
    confidence = _safe_text(payload.get("confidence"), 20) or "low"
    return {"title": title, "dateTime": date_time, "summary": summary, "basis": basis, "confidence": confidence}


async def _fill_missing_transcript_summaries() -> None:
    if not OPENAI_API_KEY:
        return
    state = _load_state()
    targets = [
        entry
        for entry in state.get("transcripts", [])
        if _safe_text(entry.get("text"), 1) and not _safe_text(entry.get("summary") or entry.get("metadataSummary"), 2)
    ]
    for target in targets[:4]:
        metadata = await _infer_transcript_metadata(str(target.get("text", "")))
        state = _load_state()
        changed = False
        for entry in state.get("transcripts", []):
            if str(entry.get("id")) != str(target.get("id")):
                continue
            summary = _safe_text(metadata.get("summary"), 700)
            if summary:
                entry["summary"] = summary
                changed = True
            if not _safe_text(entry.get("name"), 180) and metadata.get("title"):
                entry["name"] = metadata["title"]
                changed = True
            if entry.get("meetingDateTime") in (None, "", "date not stated") and metadata.get("dateTime"):
                entry["meetingDateTime"] = metadata["dateTime"]
                changed = True
            if not _safe_text(entry.get("metadataBasis"), 400) and metadata.get("basis"):
                entry["metadataBasis"] = metadata["basis"]
                changed = True
            break
        if changed:
            _save_state(state)


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
    summary = _safe_text(transcript.get("summary") or transcript.get("metadataSummary"), 700)
    basis = _safe_text(transcript.get("metadataBasis"), 400)
    meta_parts = [f"{int(transcript.get('characterCount') or 0):,} characters"]
    if meeting_time and meeting_time != "date not stated":
        meta_parts.insert(0, meeting_time)
    if created:
        meta_parts.append(f"uploaded {created}")
    story: list[Any] = [
        Paragraph(_xml_escape(title), styles["title"]),
        Paragraph(_xml_escape(" / ".join(meta_parts)), styles["meta"]),
    ]
    if summary:
        story.extend([Paragraph(_xml_escape(summary), styles["meta"]), Spacer(1, 0.08 * inch)])
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
    return [seen[key] for key in order]


def _score_calibration_instructions() -> str:
    return "\n".join(
        [
            "You calibrate easeScore and disneyScore for Alan's transcript-derived todo list.",
            "Do not add, remove, rename, merge, or split rows. Do not change the task meaning.",
            "Use only the provided task, details, time estimate, speaker, and exact evidence quote.",
            "The scores are estimates, but they must be source-bounded, internally consistent, and strict.",
            "Use the full 0-100 scale. Do not cluster most items in the 80s unless the list truly supports it.",
            "When evidence is ambiguous, lower the score instead of guessing upward.",
            "easeScore rubric:",
            "95-100 = can be finished immediately in under 10 minutes with no external dependency.",
            "85-94 = 10-30 minutes; simple edit/check with a clear target.",
            "70-84 = 30-60 minutes; clear task but needs focused work or source lookup.",
            "50-69 = 1-3 hours or requires real reasoning, figure work, data work, or careful writing.",
            "30-49 = half day or more, ambiguous, externally dependent, or requires substantial reanalysis.",
            "10-29 = blocked by missing evidence, access, advisor/tool dependency, build, experiment, or unclear acceptance.",
            "0-9 = not actionable from the transcript.",
            "disneyScore rubric:",
            "95-100 = direct major progress toward Alan's Disney/Imagineering-style future: submission-critical result, claim, figure, physical-system breakthrough, portfolio proof, or external evaluation artifact.",
            "85-94 = significant paper, research, mechanism, prototype, or career-positioning progress.",
            "70-84 = important supporting research progress: useful figure, data cleanup, caption, citation, clarity, or evidence path.",
            "50-69 = useful polish, organization, admin, or indirect support for research/career goals.",
            "30-49 = minor local cleanup or low-leverage support.",
            "10-29 = marginal connection to research, paper, future goals, or life stability.",
            "0-9 = no clear link to Alan's research/career/future goals.",
            "why must be one compact sentence explaining both scores with concrete basis.",
            "Do not write motivational copy. Do not use visible labels like Ease:, Disney:, Why:, Recommendation:, or Score basis:.",
            "Do not start many rows with the same phrase. Vary naturally and be precise.",
            "Return JSON only through the schema.",
        ]
    )


async def _calibrate_candidate_scores(
    transcript_name: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not OPENAI_API_KEY or not candidates:
        return candidates
    limited_candidates = candidates[:MAX_SCORE_CALIBRATION_ITEMS]
    candidate_payload: list[dict[str, Any]] = []
    for index, candidate in enumerate(limited_candidates):
        candidate_id = str(candidate.get("calibrationId") or f"c{index + 1}")
        candidate["calibrationId"] = candidate_id
        candidate_payload.append(
            {
                "candidateId": candidate_id,
                "task": _safe_text(candidate.get("task"), 500),
                "details": _safe_text(candidate.get("details"), 1800),
                "sourceSpeaker": _safe_text(candidate.get("sourceSpeaker"), 120),
                "timeEstimate": _safe_text(candidate.get("timeEstimate"), 80),
                "currentEaseScore": _score(candidate.get("easeScore")),
                "currentDisneyScore": _score(candidate.get("disneyScore")),
                "currentWhy": _safe_text(candidate.get("why"), 600),
                "evidenceQuote": _safe_text((candidate.get("evidence") or [""])[0], 500),
                "confidence": _safe_text(candidate.get("confidence"), 20),
            }
        )

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "maxItems": len(candidate_payload),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["candidateId", "easeScore", "disneyScore", "why"],
                    "properties": {
                        "candidateId": {"type": "string"},
                        "easeScore": {"type": "integer", "minimum": 0, "maximum": 100},
                        "disneyScore": {"type": "integer", "minimum": 0, "maximum": 100},
                        "why": {"type": "string"},
                    },
                },
            }
        },
    }
    request_body = {
        "model": OPENAI_MODEL,
        "store": False,
        "reasoning": {"effort": SCORING_REASONING_EFFORT},
        "max_output_tokens": min(18000, 900 + len(candidate_payload) * 220),
        "instructions": _score_calibration_instructions(),
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "transcriptTitle": transcript_name or "untitled transcript",
                                "items": candidate_payload,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            }
        ],
        "text": {"format": {"type": "json_schema", "name": "todo_score_calibration", "strict": True, "schema": schema}},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(160.0, connect=20.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=request_body,
            )
    except httpx.HTTPError:
        return candidates
    if response.status_code >= 400:
        return candidates
    try:
        payload = _parse_json_object(_extract_response_text(response.json()))
    except Exception:
        return candidates
    scored = payload.get("items", [])
    if not isinstance(scored, list):
        return candidates
    by_id = {str(entry.get("candidateId", "")): entry for entry in scored if isinstance(entry, dict)}
    for candidate in limited_candidates:
        entry = by_id.get(str(candidate.get("calibrationId", "")))
        if not entry:
            continue
        candidate["easeScore"] = _score(entry.get("easeScore"))
        candidate["disneyScore"] = _score(entry.get("disneyScore"))
        why = _safe_text(entry.get("why"), 1200)
        if why:
            candidate["why"] = why
    return candidates


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
                        "category",
                        "sourceSpeaker",
                        "timeEstimate",
                        "easeScore",
                        "disneyScore",
                        "why",
                        "evidenceQuote",
                        "confidence",
                    ],
                    "properties": {
                        "task": {"type": "string"},
                        "details": {"type": "string"},
                        "category": {"type": "string", "enum": ["paper", "prototype", "phd"]},
                        "sourceSpeaker": {"type": "string"},
                        "timeEstimate": {"type": "string"},
                        "easeScore": {"type": "integer", "minimum": 0, "maximum": 100},
                        "disneyScore": {"type": "integer", "minimum": 0, "maximum": 100},
                        "why": {"type": "string"},
                        "evidenceQuote": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
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
                "If an action is ambiguous and no concrete next step is stated, do not create a todo row.",
                "Do not generate questions for Alan to ask. Do not write a question list. If someone explicitly requested a follow-up check, write the check itself as the task or details.",
                "Dependent follow-up checks still count as todo rows when someone offers or requests them, such as checking a PDF after an edit, verifying a figure after export, or reviewing a citation after insertion. Mark the dependency in details instead of dropping the row.",
                "Use the speaker names in the transcript when they matter. sourceSpeaker should be the person who assigned, requested, volunteered, or clarified the action. Leave it blank only when the transcript has no speaker names.",
                "task is the thing to be done, written as a direct concrete action. Do not start the task with a speaker name; the speaker belongs in sourceSpeaker and the quote display.",
                "details must include specific context: who said what, what was decided, and what source condition matters.",
                "category must be exactly one of paper, prototype, or phd. Use paper for manuscript, CHI, PDF, figure, caption, citation, abstract, submission, advisor-comment writing/revision, explanatory paper visuals such as pictures/photos/cartoons/diagrams/plots/graphs/charts, and text or plot additions that explain prototype results. Paper beats phd and prototype when the concrete deliverable is paper text, a figure, a plot, a caption, a visual, or an explanation for the manuscript, even if the sentence mentions valves, mechanisms, compression tests, hardware, dissertation, or an advisor meeting. Use prototype only when the concrete task is to build, design, fabricate, simulate, run, measure, test, assemble, or change physical/mechanism/hardware work itself. Use phd only for proposal, dissertation, thesis, committee, defense, qualifying exam, degree-planning, or PhD program/admin work that is not primarily a paper artifact or prototype/build task.",
                "timeEstimate is a practical estimate such as 10 min, 30 min, 2 hr, half day, 1 day, or unknown.",
                "easeScore is 0-100 for how easy this is to finish quickly. Very easy immediate tasks should score high. Long, ambiguous, blocked, or emotionally heavy tasks should score lower.",
                "disneyScore is 0-100 for future-goal value, named after Alan's Disney/Imagineering goal but broader than literal Disney wording. Treat paper progress, research progress, mechanism/simulator progress, portfolio evidence, career positioning, life stability, goals, dreams, and current physical-system work as direct Disney-score evidence when the transcript supports that lane. Do not require the word Disney to appear for a paper or research task to score high. Do not give a negligible Disney score to paper, PDF, citation, figure, or research-support work merely because it is editing or checking; score minor polish moderate, claim/evidence/career-facing work high, and direct portfolio/research breakthroughs highest.",
                "why must explain both scores in one short source-grounded note. Do not write motivational copy.",
                "evidenceQuote must be an exact continuous quote copied from the transcript chunk, 12-260 characters, that supports the row. Prefer the spoken words without the speaker label when the transcript label is separate.",
                "confidence is high only when the transcript clearly supports the task, details, ease basis, and Disney-goal basis. Use medium or low for ambiguous ownership, missing date, weak ease basis, or weak Disney basis.",
                "Return JSON only through the schema. Keep strings compact. Do not include markdown.",
            ]
        ),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": input_text}]}],
        "text": {"format": {"type": "json_schema", "name": "todo_transcript_items", "strict": True, "schema": schema}},
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(220.0, connect=20.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=request_body,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="AI analysis timed out; the transcription was saved and can be retried") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="AI analysis request failed; the transcription was saved and can be retried") from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"AI analysis failed upstream ({response.status_code}); the transcription was saved and can be retried",
        )

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="AI response could not be read; the transcription was saved and can be retried") from exc
    text = _extract_response_text(response_payload)
    try:
        payload = _parse_json_object(text)
    except ValueError as exc:
        if response_payload.get("status") == "incomplete":
            raise HTTPException(status_code=502, detail="AI response was incomplete; the transcription was saved and can be retried") from exc
        raise HTTPException(status_code=502, detail="AI response could not be read; the transcription was saved and can be retried") from exc

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
                "category": _todo_category(raw),
                "sourceSpeaker": _safe_text(raw.get("sourceSpeaker"), 120),
                "timeEstimate": _safe_text(raw.get("timeEstimate"), 80),
                "easeScore": _score(raw.get("easeScore")),
                "disneyScore": _score(raw.get("disneyScore")),
                "why": _safe_text(raw.get("why"), 1200),
                "evidence": [quote] if quote else [],
                "confidence": confidence,
                "state": "needs_evidence" if quote and not verified else "review",
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
    candidates = await _calibrate_candidate_scores(name, candidates)
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
    category: str | None = Field(default=None, max_length=30)
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


@app.on_event("startup")
async def startup() -> None:
    _mark_interrupted_analyses()
    asyncio.create_task(_fill_missing_transcript_summaries())


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
    state = _load_state_for_read()
    state_items = state.get("items", [])
    return {
        "items": [_compact_item(item) for item in state_items],
        "transcripts": [_compact_transcript(entry, state_items) for entry in state.get("transcripts", [])],
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
        "summary": metadata.get("summary", ""),
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

    ACTIVE_ANALYSES.add(transcript_id)
    try:
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
    finally:
        ACTIVE_ANALYSES.discard(transcript_id)

    state = _load_state()
    for entry in state.get("transcripts", []):
        if str(entry.get("id")) == transcript_id:
            entry["status"] = "complete"
            entry["itemCount"] = len(new_items)
            entry["error"] = ""
            break
    state.setdefault("items", []).extend(new_items)
    _save_state(state)
    state_items = state.get("items", [])
    return {
        "transcript": _compact_transcript(transcript | {"status": "complete", "itemCount": len(new_items)}, state_items),
        "items": [_compact_item(item) for item in new_items],
        "allItems": [_compact_item(item) for item in state_items],
        "allTranscripts": [_compact_transcript(entry, state_items) for entry in state.get("transcripts", [])],
        "updatedAt": state["updatedAt"],
    }


@app.post("/api/todo/transcripts/{transcript_id}/retry")
async def retry_transcript_analysis(transcript_id: str) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="AI analysis is not configured")
    state = _load_state_for_read()
    transcript: dict[str, Any] | None = None
    for entry in state.get("transcripts", []):
        if str(entry.get("id")) == transcript_id:
            transcript = entry
            break
    if not transcript:
        raise HTTPException(status_code=404, detail="not found")
    if transcript.get("status") == "analyzing" and transcript_id in ACTIVE_ANALYSES:
        raise HTTPException(status_code=409, detail="analysis is still running")
    if transcript.get("status") not in {"failed", "analyzing"}:
        raise HTTPException(status_code=400, detail="only failed or interrupted transcriptions can be retried")
    text = _safe_text(transcript.get("text"), MAX_TRANSCRIPT_CHARS + 1)
    if not text:
        raise HTTPException(status_code=400, detail="saved transcription text is missing")

    transcript["status"] = "analyzing"
    transcript["error"] = ""
    transcript["model"] = OPENAI_MODEL
    transcript["chunkCount"] = len(_chunk_transcript(text))
    _save_state(state)

    ACTIVE_ANALYSES.add(transcript_id)
    try:
        try:
            new_items = await _analyze_transcript(transcript_id, _safe_text(transcript.get("name"), 180), text)
        except HTTPException as exc:
            state = _load_state()
            for entry in state.get("transcripts", []):
                if str(entry.get("id")) == transcript_id:
                    entry["status"] = "failed"
                    entry["error"] = _safe_text(exc.detail, 400)
                    break
            _save_state(state)
            raise
    finally:
        ACTIVE_ANALYSES.discard(transcript_id)

    state = _load_state()
    for entry in state.get("transcripts", []):
        if str(entry.get("id")) == transcript_id:
            entry["status"] = "complete"
            entry["itemCount"] = len(new_items)
            entry["error"] = ""
            break
    state.setdefault("items", []).extend(new_items)
    _save_state(state)
    state_items = state.get("items", [])
    return {
        "items": [_compact_item(item) for item in new_items],
        "allItems": [_compact_item(item) for item in state_items],
        "allTranscripts": [_compact_transcript(entry, state_items) for entry in state.get("transcripts", [])],
        "updatedAt": state["updatedAt"],
    }


@app.post("/api/todo/transcripts/{transcript_id}/rescore")
async def rescore_transcript_items(transcript_id: str) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="AI analysis is not configured")
    state = _load_state_for_read()
    transcript = next(
        (entry for entry in state.get("transcripts", []) if str(entry.get("id")) == transcript_id),
        None,
    )
    if not transcript:
        raise HTTPException(status_code=404, detail="not found")
    linked_items = [
        item
        for item in state.get("items", [])
        if str(item.get("sourceTranscriptId", "")) == transcript_id
    ]
    if not linked_items:
        raise HTTPException(status_code=400, detail="no todo rows to rescore")

    candidates = []
    for item in linked_items:
        evidence = item.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        candidates.append(
            {
                "calibrationId": str(item.get("id", "")),
                "task": _safe_text(item.get("task"), 800),
                "details": _safe_text(item.get("details"), 6000),
                "sourceSpeaker": _safe_text(item.get("sourceSpeaker"), 120),
                "timeEstimate": _safe_text(item.get("timeEstimate"), 80),
                "easeScore": _score(item.get("easeScore")),
                "disneyScore": _score(item.get("disneyScore")),
                "why": _safe_text(item.get("why"), 1200),
                "evidence": [_safe_text(entry, 500) for entry in evidence[:3]],
                "confidence": _safe_text(item.get("confidence") or "manual", 24),
            }
        )
    calibrated = await _calibrate_candidate_scores(_safe_text(transcript.get("name"), 180), candidates)
    by_id = {str(candidate.get("calibrationId", "")): candidate for candidate in calibrated}
    now = _now()
    changed = 0
    for item in state.get("items", []):
        candidate = by_id.get(str(item.get("id", "")))
        if not candidate:
            continue
        old = (_score(item.get("easeScore")), _score(item.get("disneyScore")), _safe_text(item.get("why"), 1200))
        item["easeScore"] = _score(candidate.get("easeScore"))
        item["disneyScore"] = _score(candidate.get("disneyScore"))
        item["why"] = _safe_text(candidate.get("why"), 1200)
        item["updatedAt"] = now
        new = (_score(item.get("easeScore")), _score(item.get("disneyScore")), _safe_text(item.get("why"), 1200))
        if new != old:
            changed += 1
    _save_state(state)
    state_items = state.get("items", [])
    return {
        "status": "ok",
        "rescored": len(linked_items),
        "changed": changed,
        "items": [_compact_item(item) for item in linked_items],
        "allItems": [_compact_item(item) for item in state_items],
        "allTranscripts": [_compact_transcript(entry, state_items) for entry in state.get("transcripts", [])],
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
            now = _now()
            for field in ["task", "details", "dateAdded", "timeEstimate", "why"]:
                value = getattr(body, field)
                if value is not None:
                    item[field] = value.strip()
            if body.category is not None:
                item["category"] = body.category.strip().lower() if body.category.strip().lower() in TODO_CATEGORIES else _todo_category(item)
            if body.easeScore is not None:
                item["easeScore"] = _score(body.easeScore)
            if body.disneyScore is not None:
                item["disneyScore"] = _score(body.disneyScore)
            if body.state is not None:
                prior_state = str(item.get("state") or "")
                next_state = body.state.strip() if body.state.strip() in allowed_states else "review"
                item["state"] = next_state
                if next_state == "done" and prior_state != "done":
                    item["doneAt"] = now
                elif next_state != "done":
                    item.pop("doneAt", None)
            item["updatedAt"] = now
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
    state_items = state.get("items", [])
    return {
        "status": "ok",
        "removedItems": removed_items,
        "items": [_compact_item(item) for item in state_items],
        "transcripts": [_compact_transcript(entry, state_items) for entry in state.get("transcripts", [])],
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
    state = _load_state_for_read()
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
