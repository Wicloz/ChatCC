"""Tracks active ChatSources and shares them across clients.

Invariants:
  * At most one ChatSource per liveChatId.
  * A source is started on its first subscriber and stopped (its streamList
    reader cancelled) the moment its last subscriber leaves — so we never keep
    burning API quota on a chat nobody is watching.
Both invariants are protected by a single lock so concurrent connect/disconnect
races can't leak or double-create sources.
"""

import asyncio
import logging

import httpx

import youtube
from chat_source import ChatSource

log = logging.getLogger("chatcc.hub")


class ChatHub:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = httpx.AsyncClient(headers={"Accept-Encoding": "identity"})
        self._sources: dict[str, ChatSource] = {}
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        async with self._lock:
            for source in list(self._sources.values()):
                await source.stop()
            self._sources.clear()
        await self._client.aclose()

    async def acquire(self, video_id: str):
        """Resolve the chat and join (or create) its shared source.

        Returns (source, queue, None) on success or (None, None, error_message).
        """
        chat_id, err = await youtube.resolve_live_chat_id(
            self._client, self._api_key, video_id
        )
        if err:
            return None, None, err

        async with self._lock:
            source = self._sources.get(chat_id)
            if source is None:
                source = ChatSource(self._client, self._api_key, chat_id)
                self._sources[chat_id] = source
                source.start()
                log.info("started source %s (now %d active)", chat_id, len(self._sources))
            queue = source.add_subscriber()
        log.info("client joined %s (%d watching)", chat_id, source.subscriber_count)
        return source, queue, None

    async def release(self, source: ChatSource, queue) -> None:
        async with self._lock:
            source.remove_subscriber(queue)
            if source.subscriber_count == 0:
                self._sources.pop(source.live_chat_id, None)
                await source.stop()
                log.info("stopped source %s (now %d active)",
                         source.live_chat_id, len(self._sources))
            else:
                log.info("client left %s (%d still watching)",
                         source.live_chat_id, source.subscriber_count)
