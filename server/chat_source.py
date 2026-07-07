"""A single shared reader for one YouTube live chat.

One ChatSource per liveChatId, regardless of how many tablets are watching it.
It runs a reconnecting streamList loop (the spike proved this is real-time
server-push: a streaming JSON array of message batches, ~10s per connection,
resumed via nextPageToken) and fans every message out to all subscriber queues.

Backpressure: subscriber queues are bounded; if a client can't keep up we drop
for that client only — never block the reader or other clients.

Lifecycle is owned by ChatHub: the source is started when its first subscriber
arrives and stopped when its last one leaves.
"""

import asyncio
import contextlib
import json
import logging
from collections import deque

import httpx

import protocol
from cctext import to_cc_text

log = logging.getLogger("chatcc.source")

STREAM_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages/stream"

BACKLOG_MAX = 50      # recent messages replayed to a newly-joined client
QUEUE_MAX = 2000      # per-subscriber buffer before we start dropping
SEEN_MAX = 4000       # message ids remembered for de-duplication


class ChatSource:
    def __init__(self, client: httpx.AsyncClient, api_key: str, live_chat_id: str):
        self._client = client
        self._api_key = api_key
        self.live_chat_id = live_chat_id

        self.subscribers: set[asyncio.Queue] = set()
        self._backlog: deque[str] = deque(maxlen=BACKLOG_MAX)
        self._state: tuple[str, str] = ("connecting", "")

        self._seen: deque[str] = deque()
        self._seen_set: set[str] = set()

        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # -- subscribers -------------------------------------------------------

    def add_subscriber(self) -> asyncio.Queue:
        """Register a client. It immediately receives current state + backlog."""
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        q.put_nowait(protocol.status(*self._state))
        for frame in self._backlog:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                break
        self.subscribers.add(q)
        return q

    def remove_subscriber(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self.subscribers)

    # -- fan-out -----------------------------------------------------------

    def _emit(self, frame: str, store: bool) -> None:
        if store:
            self._backlog.append(frame)
        for q in self.subscribers:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass  # slow client: drop for it alone

    def _set_state(self, state: str, msg: str = "") -> None:
        self._state = (state, msg)
        self._emit(protocol.status(state, msg), store=False)

    # -- reader loop -------------------------------------------------------

    async def _run(self) -> None:
        page_token: str | None = None
        backoff = 1.0
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
        try:
            while True:
                params = {
                    "liveChatId": self.live_chat_id,
                    "part": "snippet,authorDetails",
                    "key": self._api_key,
                }
                if page_token:
                    params["pageToken"] = page_token
                try:
                    async with self._client.stream(
                        "GET", STREAM_URL, params=params, timeout=timeout
                    ) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode("utf-8", "replace")
                            if resp.status_code in (403, 404):
                                # quota exhausted, chat ended, or not found: fatal.
                                log.warning("streamList %s fatal %s: %s",
                                            self.live_chat_id, resp.status_code, body[:200])
                                self._set_state("ended", f"HTTP {resp.status_code}")
                                return
                            log.warning("streamList %s HTTP %s, retrying",
                                        self.live_chat_id, resp.status_code)
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 30.0)
                            continue

                        self._set_state("live")
                        backoff = 1.0
                        page_token = await self._consume(resp, page_token)
                except (httpx.TransportError, httpx.TimeoutException) as e:
                    log.info("streamList %s transport hiccup: %r", self.live_chat_id, e)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue

                # Connection closed cleanly; reconnect promptly with the token.
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("streamList %s reader crashed", self.live_chat_id)
            self._set_state("error", "internal reader error")

    async def _consume(self, resp: httpx.Response, page_token: str | None) -> str | None:
        """Parse the streaming JSON array; emit messages; return latest token."""
        decoder = json.JSONDecoder()
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while True:
                buf = buf.lstrip()
                if buf[:1] in ("[", ","):
                    buf = buf[1:].lstrip()
                if not buf or buf[0] == "]":
                    break
                try:
                    doc, idx = decoder.raw_decode(buf)
                except json.JSONDecodeError:
                    break  # incomplete element; wait for more bytes
                buf = buf[idx:]
                if not isinstance(doc, dict):
                    continue
                if doc.get("nextPageToken"):
                    page_token = doc["nextPageToken"]
                for item in doc.get("items", []):
                    self._handle_item(item)
        return page_token

    def _handle_item(self, item: dict) -> None:
        msg_id = item.get("id")
        if msg_id:
            if msg_id in self._seen_set:
                return
            self._seen.append(msg_id)
            self._seen_set.add(msg_id)
            while len(self._seen) > SEEN_MAX:
                self._seen_set.discard(self._seen.popleft())

        snippet = item.get("snippet", {})
        author_details = item.get("authorDetails", {})
        # CC's terminal can't render arbitrary Unicode; convert to its native
        # 8-bit-safe set (emoji -> :name:, everything else unrenderable -> '?').
        author = to_cc_text(author_details.get("displayName", ""))
        text = to_cc_text(snippet.get("displayMessage") or "")

        if author_details.get("isChatOwner"):
            role = "owner"
        elif author_details.get("isChatModerator"):
            role = "moderator"
        elif author_details.get("isChatSponsor"):
            role = "member"
        elif author_details.get("isVerified"):
            role = "verified"
        else:
            role = "user"

        self._emit(
            protocol.message(msg_id or "", author, text, role,
                             snippet.get("type", "textMessageEvent")),
            store=True,
        )
