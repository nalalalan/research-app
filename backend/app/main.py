from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("TODO_DATA_DIR", "/data" if Path("/data").exists() else ROOT_DIR / ".runtime"))
STATE_PATH = Path(os.getenv("TODO_STATE_PATH", DATA_DIR / "todo_state.json"))
SERVICE_NAME = "ao-todo-list"
PASSWORD = os.getenv("TODO_PASSWORD", os.getenv("RESEARCH_PASSWORD", "031120"))
COOKIE_NAME = "todo_access"
REMEMBER_SECONDS = 60 * 60 * 24 * 180
ET = ZoneInfo("America/New_York")
SEED_TODO = "I will buy a car."


app = FastAPI(title="AO Todo", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.aolabs\.io",
    allow_origins=[
        "https://aolabs.io",
        "https://todo.aolabs.io",
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


class TodoItemIn(BaseModel):
    todo: str = Field(max_length=1200)


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _cookie_secret() -> bytes:
    configured = os.getenv("TODO_COOKIE_SECRET", os.getenv("RESEARCH_COOKIE_SECRET", "")).strip()
    seed = configured or f"{PASSWORD}:todo-cookie"
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


def _today_key() -> str:
    return _now().astimezone(ET).date().isoformat()


def _clean_text(value: str, limit: int) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())[:limit].strip()


def _new_item(todo: str, *, seed: bool = False) -> dict[str, Any]:
    created_at = _now_iso()
    return {
        "id": "seed-car" if seed else secrets.token_urlsafe(12),
        "created_at": created_at,
        "day": _today_key(),
        "todo": todo,
    }


def _storage_base() -> dict[str, Any]:
    return {
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "seeded_todo_v1": True,
        "items": [_new_item(SEED_TODO, seed=True)],
    }


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


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

    if "items" not in data and isinstance(data.get("entries"), list):
        data["items"] = [_entry_to_item(entry) for entry in data.get("entries", [])]
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if not data.get("items") and not data.get("seeded_todo_v1"):
        data["items"].append(_new_item(SEED_TODO, seed=True))
        data["seeded_todo_v1"] = True
        _save_state(data)
    return data


def _entry_to_item(entry: dict[str, Any]) -> dict[str, Any]:
    todo = entry.get("todo") or entry.get("work") or ""
    return {
        "id": entry.get("id") or secrets.token_urlsafe(12),
        "created_at": entry.get("created_at") or _now_iso(),
        "day": entry.get("day") or "",
        "todo": _clean_text(todo, 1200),
    }


def _clean_item(payload: TodoItemIn) -> dict[str, Any]:
    todo = _clean_text(payload.todo, 1200)
    if not todo:
        raise HTTPException(status_code=400, detail="missing todo")
    return _new_item(todo)


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: item.get("created_at") or "", reverse=True)


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "created_at": item.get("created_at"),
        "day": item.get("day"),
        "todo": item.get("todo") or "",
    }


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    items = _sort_items(state.get("items", []))
    latest = items[0] if items else {}
    return {
        "service": SERVICE_NAME,
        "updated_at": state.get("updated_at"),
        "item_count": len(items),
        "today": _today_key(),
        "latest": {
            "created_at": latest.get("created_at"),
            "day": latest.get("day"),
            "has_todo": bool(latest.get("todo")),
        } if latest else None,
        "source": {
            "raw_table": "password_gated",
            "public_summary": "item_count_latest_presence",
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
        "item_count": len(state.get("items", [])),
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


@app.get("/api/todo/summary")
async def todo_summary() -> dict[str, Any]:
    return _summary(_load_state())


@app.get("/api/research/summary")
async def legacy_research_summary() -> dict[str, Any]:
    return await todo_summary()


@app.get("/api/todo/items")
async def todo_items(request: Request) -> dict[str, Any]:
    _require_auth(request)
    state = _load_state()
    items = [_public_item(item) for item in _sort_items(state.get("items", []))]
    return {"items": items, "summary": _summary(state)}


@app.get("/api/research/entries")
async def legacy_research_entries(request: Request) -> dict[str, Any]:
    payload = await todo_items(request)
    return {"entries": payload["items"], "summary": payload["summary"]}


@app.post("/api/todo/items")
async def todo_item_create(payload: TodoItemIn, request: Request) -> dict[str, Any]:
    _require_auth(request)
    state = _load_state()
    item = _clean_item(payload)
    state.setdefault("items", []).append(item)
    state["items"] = _sort_items(state["items"])[:5000]
    _save_state(state)
    return {"ok": True, "item": _public_item(item), "summary": _summary(state)}


@app.delete("/api/todo/items/{item_id}")
async def todo_item_delete(item_id: str, request: Request) -> dict[str, Any]:
    _require_auth(request)
    state = _load_state()
    items = state.get("items", [])
    next_items = [item for item in items if item.get("id") != item_id]
    if len(next_items) == len(items):
        raise HTTPException(status_code=404, detail="item not found")
    state["items"] = next_items
    _save_state(state)
    return {"ok": True, "summary": _summary(state)}


@app.delete("/api/research/entries/{entry_id}")
async def legacy_research_entry_delete(entry_id: str, request: Request) -> dict[str, Any]:
    return await todo_item_delete(entry_id, request)


@app.get("/{asset_path:path}")
async def static_asset(asset_path: str) -> FileResponse:
    allowed_suffixes = {".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".webmanifest", ".html"}
    path = (ROOT_DIR / asset_path).resolve()
    if ROOT_DIR not in path.parents and path != ROOT_DIR:
        raise HTTPException(status_code=404, detail="not found")
    if path.exists() and path.is_file() and path.suffix.lower() in allowed_suffixes:
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="not found")
