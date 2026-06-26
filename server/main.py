"""ChatCC Phase 1 server: read-only YouTube live chat for ComputerCraft tablets.

Endpoints:
    GET  /install          -> installer Lua, with this server's URL injected
    GET  /client           -> the chat client Lua ('ytchat'), URL injected
    WS   /ws/chat?v=<url>  -> live chat stream for one video, opcode-framed

The API key is read from the YT_API_KEY environment variable (loaded from .env
via python-dotenv). It never appears in any served file or client.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

import protocol
import youtube
from hub import ChatHub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("chatcc")

LUA_DIR = Path(__file__).resolve().parent.parent / "lua"
ENV_VAR = "YT_API_KEY"

hub: ChatHub


@asynccontextmanager
async def lifespan(app: FastAPI):
    global hub
    load_dotenv()
    api_key = os.environ.get(ENV_VAR)
    if not api_key:
        raise RuntimeError(f"{ENV_VAR} is not set (checked .env and environment)")
    hub = ChatHub(api_key)
    log.info("ChatCC server ready")
    try:
        yield
    finally:
        await hub.aclose()


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
            await ws.receive_text()  # clients send nothing in Phase 1; just detect close
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
        watch = asyncio.create_task(_recv_until_close(ws))
        done, pending = await asyncio.wait(
            {pump, watch}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.release(source, queue)
