"""Live integration tests against real 24/7 YouTube streams.

Run with:  pytest -m youtube
Skipped automatically if YT_API_KEY is not configured. The key is never logged.
"""

import asyncio
import json
import os

import httpx
import pytest
from dotenv import load_dotenv

import main
import oauth
import youtube
from chat_source import ChatSource

STREAMS = [
    "https://www.youtube.com/watch?v=qso6aqGsjwc",
    "https://www.youtube.com/watch?v=HlJ4aRtYhoo",
]


def _key():
    load_dotenv()
    return os.environ.get("YT_API_KEY")


requires_key = pytest.mark.skipif(not _key(), reason="YT_API_KEY not set")
pytestmark = pytest.mark.youtube


@requires_key
@pytest.mark.parametrize("url", STREAMS)
def test_resolve_live_chat_id(url):
    async def go():
        async with httpx.AsyncClient() as client:
            vid = youtube.extract_video_id(url)
            assert vid, f"could not parse id from {url}"
            chat_id, err = await youtube.resolve_live_chat_id(client, _key(), vid)
            assert err is None, err
            assert chat_id
    asyncio.run(go())


@requires_key
def test_chat_source_reaches_live_and_parses():
    async def go():
        client = httpx.AsyncClient(headers={"Accept-Encoding": "identity"})
        vid = youtube.extract_video_id(STREAMS[0])
        chat_id, err = await youtube.resolve_live_chat_id(client, _key(), vid)
        assert err is None, err

        source = ChatSource(client, _key(), chat_id)
        source.start()
        q = source.add_subscriber()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 40
        live = False
        msgs = 0
        try:
            while loop.time() < deadline:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                op, data = frame[:1], json.loads(frame[1:])
                if op == "S":
                    if data["s"] == "live":
                        live = True
                    if data["s"] in ("error", "ended"):
                        pytest.fail(f"source reported {data['s']}: {data.get('m')}")
                elif op == "M":
                    msgs += 1
                    assert "a" in data and "m" in data and "r" in data
                if live and msgs >= 1:
                    break
        finally:
            await source.stop()
            await client.aclose()

        assert live, "never reached live state within timeout"
        # Messages are a bonus on low-traffic streams; log for visibility.
        print(f"\n[integration] reached live; received {msgs} message(s)")
    asyncio.run(go())


@requires_key
def test_ws_endpoint_streams_live():
    from fastapi.testclient import TestClient

    vid = youtube.extract_video_id(STREAMS[1])
    with TestClient(main.app) as client:
        with client.websocket_connect(f"/ws/chat?v={vid}") as ws:
            live = False
            for _ in range(30):
                frame = ws.receive_text()
                if frame[:1] == "S" and json.loads(frame[1:])["s"] == "live":
                    live = True
                    break
            assert live, "ws never delivered a live status"


def _oauth_creds():
    load_dotenv()
    return os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")


requires_oauth = pytest.mark.skipif(
    not all(_oauth_creds()), reason="GOOGLE_CLIENT_ID/SECRET not set"
)


@requires_oauth
def test_device_code_and_one_poll():
    """Verify the OAuth client works end-to-end without human consent:
    request a device code, then poll once and expect authorization_pending."""
    client_id, client_secret = _oauth_creds()

    async def go():
        async with httpx.AsyncClient() as client:
            info = await oauth.request_device_code(client, client_id)
            assert info["user_code"] and info["device_code"]
            assert info["verification_url"]
            status, _ = await oauth.poll_token(client, client_id, client_secret, info["device_code"])
            # Nobody has consented in this split second, so it must be pending.
            assert status == oauth.PENDING, f"unexpected poll status: {status}"
    asyncio.run(go())

