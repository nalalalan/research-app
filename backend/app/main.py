from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("RESEARCH_DATA_DIR", "/data" if Path("/data").exists() else ROOT_DIR / ".runtime"))
STATE_PATH = Path(os.getenv("RESEARCH_STATE_PATH", DATA_DIR / "research_state.json"))
SERVICE_NAME = "ao-research-ledger"
PASSWORD = os.getenv("RESEARCH_PASSWORD", "031120")
COOKIE_NAME = "research_access"
REMEMBER_SECONDS = 60 * 60 * 24 * 180
ET = ZoneInfo("America/New_York")

CATEGORIES: dict[str, dict[str, str]] = {
    "imagineer": {"label": "imagineer", "accent": "#66799f"},
    "fluxcell": {"label": "fluxcell", "accent": "#648f89"},
    "sarrus": {"label": "sarrus", "accent": "#81977f"},
}
STATUSES = {"moved", "planned", "blocked", "done"}


app = FastAPI(title="AO Research Ledger", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.aolabs\.io",
    allow_origins=[
        "https://aolabs.io",
        "https://research.aolabs.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8783",
        "http://127.0.0.1:8783",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


class AuthPayload(BaseModel):
    password: str = Field(default="", max_length=100)
    remember: bool = True


class ResearchEntryIn(BaseModel):
    category: str = Field(max_length=40)
    work: str = Field(max_length=1200)
    artifact_url: str = Field(default="", max_length=800)
    next_step: str = Field(default="", max_length=800)
    status: str = Field(default="moved", max_length=40)
    minutes: int | None = Field(default=None, ge=0, le=1440)


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _cookie_secret() -> bytes:
    configured = os.getenv("RESEARCH_COOKIE_SECRET", "").strip()
    seed = configured or f"{PASSWORD}:research-ledger-cookie"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _sign_device(device_id: str) -> str:
    return hmac.new(_cookie_secret(), device_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _valid_cookie(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    device_id, signature = value.split(".", 1)
    if not device_id or not signature:
        return False
    expected = _sign_device(device_id)
    return hmac.compare_digest(signature, expected)


def _issue_cookie(response: Response, remember: bool) -> None:
    device_id = secrets.token_urlsafe(24)
    signed = f"{device_id}.{_sign_device(device_id)}"
    kwargs: dict[str, Any] = {
        "key": COOKIE_NAME,
        "value": signed,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }
    if remember:
        kwargs["max_age"] = REMEMBER_SECONDS
    response.set_cookie(**kwargs)


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _require_auth(request: Request) -> None:
    if not _valid_cookie(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="locked")


def _storage_base() -> dict[str, Any]:
    return {
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "entries": [],
    }


def _ensure_storage() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        _save_state(_storage_base())


def _load_state() -> dict[str, Any]:
    _ensure_storage()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = STATE_PATH.with_suffix(f".broken-{_now().strftime('%Y%m%d%H%M%S')}.json")
        STATE_PATH.replace(backup)
        data = _storage_base()
        _save_state(data)
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    return data


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day_key(value: str | None) -> str:
    parsed = _parse_time(value)
    if not parsed:
        return ""
    return parsed.astimezone(ET).date().isoformat()


def _today_key() -> str:
    return _now().astimezone(ET).date().isoformat()


def _clean_text(value: str, limit: int) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())[:limit].strip()


def _clean_entry(payload: ResearchEntryIn) -> dict[str, Any]:
    category = payload.category.strip().lower()
    status = payload.status.strip().lower()
    work = _clean_text(payload.work, 1200)
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="invalid category")
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    if not work:
        raise HTTPException(status_code=400, detail="missing work")
    created_at = _now_iso()
    return {
        "id": secrets.token_urlsafe(12),
        "created_at": created_at,
        "day": _day_key(created_at),
        "category": category,
        "work": work,
        "artifact_url": _clean_text(payload.artifact_url, 800),
        "next_step": _clean_text(payload.next_step, 800),
        "status": status,
        "minutes": payload.minutes,
    }


def _entry_public_view(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("id"),
        "created_at": entry.get("created_at"),
        "day": entry.get("day") or _day_key(entry.get("created_at")),
        "category": entry.get("category"),
        "status": entry.get("status"),
        "work": entry.get("work") or "",
        "artifact_url": entry.get("artifact_url") or "",
        "next_step": entry.get("next_step") or "",
        "minutes": entry.get("minutes"),
    }


def _sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda item: item.get("created_at") or "", reverse=True)


def _streak_days(entries: list[dict[str, Any]]) -> int:
    days = {
        item.get("day") or _day_key(item.get("created_at"))
        for item in entries
        if item.get("created_at")
    }
    days.discard("")
    current = _now().astimezone(ET).date()
    streak = 0
    while current.isoformat() in days:
        streak += 1
        current = current - timedelta(days=1)
    return streak


def _category_summary(entries: list[dict[str, Any]], category: str) -> dict[str, Any]:
    subset = _sort_entries([item for item in entries if item.get("category") == category])
    latest = subset[0] if subset else {}
    today = _today_key()
    return {
        "label": CATEGORIES[category]["label"],
        "accent": CATEGORIES[category]["accent"],
        "count": len(subset),
        "today_count": len([item for item in subset if (item.get("day") or _day_key(item.get("created_at"))) == today]),
        "minutes": sum(int(item.get("minutes") or 0) for item in subset),
        "latest": _entry_public_view(latest) if latest else None,
    }


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    entries = _sort_entries(state.get("entries", []))
    today = _today_key()
    latest = entries[0] if entries else {}
    return {
        "service": SERVICE_NAME,
        "updated_at": state.get("updated_at"),
        "entry_count": len(entries),
        "today": today,
        "today_count": len([item for item in entries if (item.get("day") or _day_key(item.get("created_at"))) == today]),
        "streak_days": _streak_days(entries),
        "minutes": sum(int(item.get("minutes") or 0) for item in entries),
        "categories": {key: _category_summary(entries, key) for key in CATEGORIES},
        "latest": _entry_public_view(latest) if latest else None,
        "source": {
            "raw_table": "password_gated",
            "public_summary": "category_counts_latest_entries",
            "timezone": "America/New_York",
        },
    }


@app.on_event("startup")
async def startup() -> None:
    _ensure_storage()


@app.get("/health")
async def health() -> dict[str, Any]:
    state = _load_state()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "entry_count": len(state.get("entries", [])),
        "storage": str(STATE_PATH),
    }


@app.get("/")
async def index(request: Request) -> FileResponse:
    if _valid_cookie(request.cookies.get(COOKIE_NAME)):
        return FileResponse(ROOT_DIR / "index.html")
    return FileResponse(ROOT_DIR / "gate.html")


@app.post("/api/auth")
async def auth(payload: AuthPayload, response: Response) -> dict[str, Any]:
    if not hmac.compare_digest(payload.password, PASSWORD):
        raise HTTPException(status_code=403, detail="wrong password")
    _issue_cookie(response, remember=payload.remember)
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, Any]:
    _clear_cookie(response)
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status(request: Request) -> dict[str, bool]:
    return {"authenticated": _valid_cookie(request.cookies.get(COOKIE_NAME))}


@app.get("/api/research/summary")
async def research_summary() -> dict[str, Any]:
    return _summary(_load_state())


@app.get("/api/research/entries")
async def research_entries(request: Request, category: str | None = None) -> dict[str, Any]:
    _require_auth(request)
    state = _load_state()
    entries = _sort_entries(state.get("entries", []))
    if category:
        clean_category = category.strip().lower()
        if clean_category not in CATEGORIES:
            raise HTTPException(status_code=400, detail="invalid category")
        entries = [entry for entry in entries if entry.get("category") == clean_category]
    return {"entries": entries, "summary": _summary(state)}


@app.post("/api/research/entries")
async def research_entry_create(payload: ResearchEntryIn, request: Request) -> dict[str, Any]:
    _require_auth(request)
    state = _load_state()
    entry = _clean_entry(payload)
    state.setdefault("entries", []).append(entry)
    state["entries"] = _sort_entries(state["entries"])[:5000]
    _save_state(state)
    return {"ok": True, "entry": entry, "summary": _summary(state)}


@app.delete("/api/research/entries/{entry_id}")
async def research_entry_delete(entry_id: str, request: Request) -> dict[str, Any]:
    _require_auth(request)
    state = _load_state()
    entries = state.get("entries", [])
    next_entries = [entry for entry in entries if entry.get("id") != entry_id]
    if len(next_entries) == len(entries):
        raise HTTPException(status_code=404, detail="entry not found")
    state["entries"] = next_entries
    _save_state(state)
    return {"ok": True, "summary": _summary(state)}


@app.get("/{asset_path:path}")
async def static_asset(asset_path: str) -> FileResponse:
    allowed_suffixes = {".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".webmanifest", ".html"}
    path = (ROOT_DIR / asset_path).resolve()
    if ROOT_DIR not in path.parents and path != ROOT_DIR:
        raise HTTPException(status_code=404, detail="not found")
    if path.exists() and path.is_file() and path.suffix.lower() in allowed_suffixes:
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="not found")

