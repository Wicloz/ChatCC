"""ChatCC server: YouTube live chat for ComputerCraft tablets.

Endpoints:
    GET  /install          -> installer Lua, with this server's URL injected
    GET  /client           -> the chat client Lua ('ytchat'), URL injected
    WS   /ws/chat?v=<url>  -> live chat stream for one video, opcode-framed
    WS   /ws/login         -> OAuth device-flow login (returns a bearer token)

Secrets are read from the environment (loaded from .env via python-dotenv) and
never appear in any served file or client:
    YT_API_KEY            -> reading chat (Phase 1)
    GOOGLE_CLIENT_ID      -> OAuth device flow (Phase 2, optional)
    GOOGLE_CLIENT_SECRET  -> OAuth device flow (Phase 2, optional)
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

import login
import protocol
import youtube
from credentials import CredentialStore
from hub import ChatHub
from sender import Sender

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("chatcc")

LUA_DIR = Path(__file__).resolve().parent.parent / "lua"
ENV_VAR = "YT_API_KEY"

hub: ChatHub
store: CredentialStore
oauth_http: httpx.AsyncClient
sender: Sender | None = None
CLIENT_ID: str | None = None
CLIENT_SECRET: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global hub, store, oauth_http, sender, CLIENT_ID, CLIENT_SECRET
    load_dotenv()
    api_key = os.environ.get(ENV_VAR)
    if not api_key:
        raise RuntimeError(f"{ENV_VAR} is not set (checked .env and environment)")
    hub = ChatHub(api_key)

    CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    data_dir = Path(os.environ.get("CHATCC_DATA_DIR", "data"))
    store = CredentialStore(data_dir / "credentials.json")
    oauth_http = httpx.AsyncClient()
    if CLIENT_ID and CLIENT_SECRET:
        burst = float(os.environ.get("CHATCC_SEND_BURST", "5"))
        interval = float(os.environ.get("CHATCC_SEND_INTERVAL", "2"))
        sender = Sender(oauth_http, CLIENT_ID, CLIENT_SECRET, store,
                        burst=burst, interval=interval)
    else:
        sender = None
    log.info("ChatCC server ready (login/send %s)",
             "enabled" if sender else "disabled — set GOOGLE_CLIENT_ID/SECRET")
    try:
        yield
    finally:
        await hub.aclose()
        await oauth_http.aclose()


app = FastAPI(lifespan=lifespan)


# -- Lua delivery (LiveCC pattern: server injects its own public URL) --------

def _server_base_url(request: Request) -> str:
    """Public base URL, honouring a TLS-terminating reverse proxy."""
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if proto and host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


def _serve_lua(name: str, request: Request) -> PlainTextResponse:
    template = (LUA_DIR / name).read_text(encoding="utf-8")
    body = template.replace('"{{SERVER}}"', f'"{_server_base_url(request)}"')
    return PlainTextResponse(body)


@app.get("/install")
async def install(request: Request):
    return _serve_lua("install.lua", request)


@app.get("/client")
async def client(request: Request):
    return _serve_lua("client.lua", request)


# -- WebSocket ---------------------------------------------------------------

async def _pump(ws: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        frame = await queue.get()
        await ws.send_text(frame)


async def _recv_until_close(ws: WebSocket) -> None:
    try:
        while True:
            await ws.receive_text()  # just detect close
    except WebSocketDisconnect:
        return


async def _post_message(ws: WebSocket, source, auth_token: str, text: str) -> None:
    if sender is None:
        await ws.send_text(protocol.status("error", "sending is not configured on this server"))
        return
    ok, err = await sender.send(auth_token, source.live_chat_id, text)
    if not ok:
        await ws.send_text(protocol.status("error", err or "send failed"))
    # On success the message echoes back through the live stream, so no ack needed.


async def _recv_chat(ws: WebSocket, source) -> None:
    """Handle client->server frames on the chat socket. 'P' = post a message."""
    try:
        while True:
            raw = await ws.receive_text()
            if not raw:
                continue
            if raw[:1] == "P":
                try:
                    data = json.loads(raw[1:])
                except json.JSONDecodeError:
                    continue
                await _post_message(ws, source, data.get("k", ""), data.get("m", ""))
    except WebSocketDisconnect:
        return


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    video_id = youtube.extract_video_id(ws.query_params.get("v", ""))
    if not video_id:
        await ws.send_text(protocol.status("error", "missing or invalid video id"))
        await ws.close()
        return

    source, queue, err = await hub.acquire(video_id)
    if err:
        await ws.send_text(protocol.status("error", err))
        await ws.close()
        return

    try:
        pump = asyncio.create_task(_pump(ws, queue))
        watch = asyncio.create_task(_recv_chat(ws, source))
        done, pending = await asyncio.wait(
            {pump, watch}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.release(source, queue)


@app.websocket("/ws/login")
async def ws_login(ws: WebSocket):
    await ws.accept()
    if not (CLIENT_ID and CLIENT_SECRET):
        await ws.send_text(protocol.status("error", "login is not configured on this server"))
        await ws.close()
        return

    flow = asyncio.create_task(
        login.perform_device_login(ws, oauth_http, CLIENT_ID, CLIENT_SECRET, store)
    )
    watch = asyncio.create_task(_recv_until_close(ws))
    try:
        done, pending = await asyncio.wait({flow, watch}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        # Surface a login crash instead of swallowing it.
        if flow in done:
            flow.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("login flow failed")
        try:
            await ws.send_text(protocol.status("error", "internal login error"))
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
