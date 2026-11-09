"""The two web apps. Only the team app is ever exposed through the tunnel."""

from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import segno

from server.containers import DockerError
from server.event import Event, EventError
from server.llm import azure_client
from server.llm_check import run_checks


HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
ADMIN_STATIC = HERE / "admin_static"
CHEATSHEET = HERE / "chat_context" / "cheatsheet.md"
ADMIN_HEADER = "x-smash-admin"
CLOSE_CODE_UNKNOWN_TEAM = 4404
LLM_TEST_TIMEOUT_SECONDS = 60.0

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

NOT_FOUND_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Smash Bots</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/retro.css"></head>
<body class="center-page"><div class="panel"><h1>NO TEAM HERE</h1>
<p>Open the link or QR code your team was given.</p></div></body></html>"""


def _new_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    return app


def _known_session(value: str) -> str | None:
    """A phone's saved conversation id, if it is a well-formed one."""
    try:
        return str(uuid.UUID(value)) == value and value or None
    except ValueError:
        return None


def _qr_svg(link: str) -> Response:
    buffer = io.BytesIO()
    segno.make(link, error="m").save(buffer, kind="svg", scale=6, border=2, dark="#26242b", light="#e6dfcd")
    return Response(buffer.getvalue(), media_type="image/svg+xml")


def create_team_app(event: Event) -> FastAPI:
    app = _new_app()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return HTMLResponse(NOT_FOUND_PAGE, status_code=404)

    @app.get("/t/{token}", response_model=None)
    async def team_page(token: str):
        if event.db.team_by_token(token) is None:
            return HTMLResponse(NOT_FOUND_PAGE, status_code=404)
        return FileResponse(HERE / "static" / "team.html")

    @app.get("/c/{token}", response_model=None)
    async def chat_page(token: str):
        if not event.chat_token_valid(token):
            return HTMLResponse(NOT_FOUND_PAGE, status_code=404)
        return FileResponse(HERE / "static" / "chat.html")

    @app.websocket("/ws-chat/{token}")
    async def chat_socket(socket: WebSocket, token: str, session: str = "") -> None:
        await socket.accept()
        if not event.chat_token_valid(token):
            await socket.close(code=CLOSE_CODE_UNKNOWN_TEAM)
            return
        session = _known_session(session) or str(uuid.uuid4())
        event.hub.add_chat(session, socket)
        await event.hub.to_admins(event.admin_state())
        try:
            await socket.send_json({"type": "session", "id": session})
            await socket.send_json({"type": "chat_history", "messages": event.db.chat_history(session)})
            await socket.send_json(event.chat_state(session))
            while True:
                try:
                    message = await socket.receive_json()
                except ValueError:
                    continue
                if isinstance(message, dict):
                    await event.chat_message(session, socket, message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            event.hub.remove_chat(session, socket)
            await event.hub.to_admins(event.admin_state())

    @app.get("/cheatsheet.md", response_class=PlainTextResponse)
    async def cheatsheet() -> PlainTextResponse:
        return PlainTextResponse(CHEATSHEET.read_text())

    @app.websocket("/ws/{token}")
    async def team_socket(socket: WebSocket, token: str) -> None:
        team = event.db.team_by_token(token)
        await socket.accept()
        if team is None:
            await socket.close(code=CLOSE_CODE_UNKNOWN_TEAM)
            return
        team_id = team["id"]
        event.hub.add_team(team_id, socket)
        await event.hub.to_admins(event.admin_state())
        try:
            await socket.send_json({"type": "agent_activity_history", "entries": event.team_activity(team_id)})
            await socket.send_json(event.team_state(team))
            while True:
                try:
                    message = await socket.receive_json()
                except ValueError:
                    continue
                if isinstance(message, dict):
                    await event.team_message(event.db.team(team_id), socket, message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            event.hub.remove_team(team_id, socket)
            await event.hub.to_admins(event.admin_state())

    return app


class NameBody(BaseModel):
    name: str = Field(max_length=40)


class NewTeamBody(NameBody):
    character: str = Field(max_length=40)


class CharacterBody(BaseModel):
    character: str = Field(max_length=40)


class TimeBody(BaseModel):
    seconds: int = Field(ge=1, le=1800)


class MatchBody(BaseModel):
    team_ids: list[int]


class RollbackBody(BaseModel):
    version_id: int


class SettingsBody(BaseModel):
    prompt_seconds: int | None = Field(default=None, ge=15, le=7200)
    agent_timeout_seconds: int | None = Field(default=None, ge=30, le=1800)
    match_seconds: int | None = Field(default=None, ge=10, le=480)
    azure_base_url: str | None = Field(default=None, max_length=300)
    azure_api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    chat_enabled: bool | None = None
    public_url: str | None = Field(default=None, max_length=300)
    iso_path: str | None = Field(default=None, max_length=1000)
    record_replays: bool | None = None


class LlmTestBody(BaseModel):
    """Values to try before saving; anything left out uses the saved settings."""
    azure_base_url: str | None = Field(default=None, max_length=300)
    azure_api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=100)


def _require_web_url(url: str | None, label: str) -> None:
    if url and urlsplit(url).scheme not in ("http", "https"):
        raise EventError(f"The {label} must start with https://")


def create_admin_app(event: Event, *, port: int) -> FastAPI:
    app = _new_app()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/admin-static", StaticFiles(directory=ADMIN_STATIC), name="admin-static")

    @app.middleware("http")
    async def local_only(request: Request, call_next) -> Response:
        # A web page cannot set this header cross-site without a CORS preflight,
        # which this app never approves; the Host check stops DNS rebinding.
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"detail": "Admin is only available on this machine."}, status_code=403)
        if request.method not in ("GET", "HEAD") and request.headers.get(ADMIN_HEADER) != "1":
            return JSONResponse({"detail": "Missing admin header."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(EventError)
    @app.exception_handler(DockerError)
    async def event_error(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/")
    async def admin_page() -> FileResponse:
        return FileResponse(ADMIN_STATIC / "admin.html")

    @app.websocket("/ws")
    async def admin_socket(socket: WebSocket) -> None:
        origin_host = urlsplit(socket.headers.get("origin", "")).netloc
        if socket.headers.get("host") not in allowed_hosts or origin_host not in allowed_hosts:
            await socket.close(code=1008)
            return
        await socket.accept()
        event.hub.add_admin(socket)
        try:
            await socket.send_json(event.admin_state())
            await socket.send_json({"type": "agent_activity_history", "entries": event.admin_activity()})
            while True:
                await socket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            event.hub.remove_admin(socket)

    @app.post("/api/teams")
    async def add_team(body: NewTeamBody) -> dict:
        team = await event.add_team(body.name, body.character)
        return {"id": team["id"]}

    @app.put("/api/teams/{team_id}")
    async def rename_team(team_id: int, body: NameBody) -> dict:
        await event.rename_team(team_id, body.name)
        return {"ok": True}

    @app.put("/api/teams/{team_id}/character")
    async def set_character(team_id: int, body: CharacterBody) -> dict:
        await event.set_character(team_id, body.character)
        return {"ok": True}

    @app.delete("/api/teams/{team_id}")
    async def delete_team(team_id: int) -> dict:
        await event.delete_team(team_id)
        return {"ok": True}

    @app.post("/api/teams/{team_id}/regenerate")
    async def regenerate(team_id: int) -> dict:
        await event.regenerate_link(team_id)
        return {"ok": True}

    @app.get("/api/teams/{team_id}/qr")
    async def team_qr(team_id: int) -> Response:
        team = event.db.team(team_id)
        if team is None:
            raise HTTPException(status_code=404)
        return _qr_svg(event.team_link(team, event.db.settings()))

    @app.get("/api/teams/{team_id}/detail")
    async def team_detail(team_id: int) -> dict:
        return event.team_detail(team_id)

    @app.post("/api/teams/{team_id}/rollback")
    async def rollback(team_id: int, body: RollbackBody) -> dict:
        await event.rollback(team_id, body.version_id)
        return {"ok": True}

    @app.post("/api/teams/{team_id}/retry")
    async def retry(team_id: int) -> dict:
        await event.retry_agent(team_id)
        return {"ok": True}

    @app.get("/api/chat/qr")
    async def chat_qr() -> Response:
        return _qr_svg(event.chat_link(event.db.settings()))

    @app.get("/api/chat/log")
    async def chat_log() -> dict:
        return {"messages": event.db.chat_log()}

    @app.post("/api/chat/regenerate")
    async def regenerate_chat() -> dict:
        await event.regenerate_chat_link()
        return {"ok": True}

    @app.post("/api/llm/test")
    async def test_llm(body: LlmTestBody) -> dict:
        saved = event.db.settings()
        base_url = saved["azure_base_url"] if body.azure_base_url is None else body.azure_base_url
        _require_web_url(base_url, "Azure base URL")
        client = azure_client(
            base_url, body.azure_api_key or saved["azure_api_key"],
            timeout=LLM_TEST_TIMEOUT_SECONDS, max_retries=0,
        )
        if client is None:
            raise EventError("Enter the Azure base URL and API key first.")
        return {"checks": await run_checks(client, body.model or saved["model"])}

    @app.put("/api/settings")
    async def update_settings(body: SettingsBody) -> dict:
        values = body.model_dump(exclude_none=True)
        _require_web_url(values.get("public_url"), "public URL")
        _require_web_url(values.get("azure_base_url"), "Azure base URL")
        event.db.update_settings(values)
        if "azure_base_url" in values or "azure_api_key" in values:
            saved = event.db.settings()
            event.set_client(azure_client(saved["azure_base_url"], saved["azure_api_key"]))
        await event.broadcast()
        return {"ok": True}

    @app.post("/api/round/open")
    async def open_round() -> dict:
        await event.open_round()
        return {"ok": True}

    @app.post("/api/round/close")
    async def close_round() -> dict:
        await event.close_round()
        return {"ok": True}

    @app.post("/api/round/add-time")
    async def add_time(body: TimeBody) -> dict:
        await event.add_time(body.seconds)
        return {"ok": True}

    @app.post("/api/match/start")
    async def start_match(body: MatchBody) -> dict:
        await event.start_match(body.team_ids)
        return {"ok": True}

    @app.post("/api/match/stop")
    async def stop_match() -> dict:
        event.stop_match()
        return {"ok": True}

    @app.post("/api/reset")
    async def reset() -> dict:
        await event.reset()
        return {"ok": True}

    return app
