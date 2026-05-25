from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
COOKIE_NAME = "ao_todo_device"
SESSION_SECONDS = 60 * 60 * 24 * 180
PASSWORD = os.getenv("TODO_PASSWORD", "031120").strip()
COOKIE_SECRET = os.getenv("TODO_COOKIE_SECRET", "local-todo-cookie-secret").strip()


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


def _default_state() -> dict[str, Any]:
    return {"items": [], "updatedAt": _now()}


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    items = data.get("items")
    if not isinstance(items, list):
        data["items"] = []
    data.setdefault("updatedAt", _now())
    return data


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = _now()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _sign(expires: int) -> str:
    digest = hmac.new(COOKIE_SECRET.encode("utf-8"), str(expires).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{expires}.{digest}"


def _valid_cookie(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    expires_text, signature = value.split(".", 1)
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    expected = _sign(expires).split(".", 1)[1]
    return hmac.compare_digest(signature, expected)


def _require_auth(cookie: str | None) -> None:
    if not _valid_cookie(cookie):
        raise HTTPException(status_code=401, detail="locked")


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")),
        "item": str(item.get("item", "")),
        "status": str(item.get("status", "")),
        "createdAt": str(item.get("createdAt", "")),
        "updatedAt": str(item.get("updatedAt", "")),
    }


class AuthBody(BaseModel):
    password: str = Field(max_length=80)
    remember: bool = True


class CreateItemBody(BaseModel):
    item: str = Field(min_length=1, max_length=500)
    status: str = Field(default="", max_length=30000)


class UpdateItemBody(BaseModel):
    item: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, max_length=30000)


app = FastAPI(title="AO Todo", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://aolabs.io", "https://todo.aolabs.io", "http://localhost:8783", "http://127.0.0.1:8783"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ao-todo"}


@app.get("/")
async def index(ao_todo_device: str | None = Cookie(default=None)) -> FileResponse:
    if not _valid_cookie(ao_todo_device):
        return FileResponse(ROOT_DIR / "gate.html")
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/api/session")
async def session(ao_todo_device: str | None = Cookie(default=None)) -> dict[str, bool]:
    return {"authenticated": _valid_cookie(ao_todo_device)}


@app.post("/api/auth")
async def auth(body: AuthBody, request: Request, response: Response) -> dict[str, str]:
    if not secrets.compare_digest(body.password.strip(), PASSWORD):
        raise HTTPException(status_code=403, detail="wrong password")
    expires = int(time.time()) + SESSION_SECONDS
    response.set_cookie(
        COOKIE_NAME,
        _sign(expires),
        max_age=SESSION_SECONDS if body.remember else None,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return {"status": "ok"}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE_NAME)
    return {"status": "ok"}


@app.get("/api/todo/items")
async def list_items(ao_todo_device: str | None = Cookie(default=None)) -> dict[str, Any]:
    _require_auth(ao_todo_device)
    state = _load_state()
    return {"items": [_compact_item(item) for item in state.get("items", [])], "updatedAt": state.get("updatedAt")}


@app.post("/api/todo/items")
async def create_item(body: CreateItemBody, ao_todo_device: str | None = Cookie(default=None)) -> dict[str, Any]:
    _require_auth(ao_todo_device)
    state = _load_state()
    item = {
        "id": secrets.token_hex(8),
        "item": body.item.strip(),
        "status": body.status,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    state.setdefault("items", []).append(item)
    _save_state(state)
    return {"item": _compact_item(item), "updatedAt": state["updatedAt"]}


@app.patch("/api/todo/items/{item_id}")
async def update_item(item_id: str, body: UpdateItemBody, ao_todo_device: str | None = Cookie(default=None)) -> dict[str, Any]:
    _require_auth(ao_todo_device)
    state = _load_state()
    for item in state.get("items", []):
        if str(item.get("id")) == item_id:
            if body.item is not None:
                item["item"] = body.item.strip()
            if body.status is not None:
                item["status"] = body.status
            item["updatedAt"] = _now()
            _save_state(state)
            return {"item": _compact_item(item), "updatedAt": state["updatedAt"]}
    raise HTTPException(status_code=404, detail="not found")


@app.delete("/api/todo/items/{item_id}")
async def delete_item(item_id: str, ao_todo_device: str | None = Cookie(default=None)) -> dict[str, Any]:
    _require_auth(ao_todo_device)
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
    items = state.get("items", [])
    latest = items[-1] if items else {}
    return {
        "service": "ao-todo",
        "count": len(items),
        "updatedAt": state.get("updatedAt"),
        "latestItem": str(latest.get("item", ""))[:120],
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
