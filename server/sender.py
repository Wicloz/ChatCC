"""Sends messages on behalf of a logged-in tablet.

Given the tablet's bearer auth-token, looks up the stored Google refresh token,
exchanges it for an access token (cached until shortly before expiry), and posts
to the live chat. The tablet never sees the access or refresh token.
"""

import asyncio
import logging
import time

import oauth

log = logging.getLogger("chatcc.sender")

MAX_LEN = 200  # YouTube live chat messages are capped at 200 chars.


class Sender:
    def __init__(self, client, client_id, client_secret, store):
        self._client = client
        self._cid = client_id
        self._csecret = client_secret
        self._store = store
        self._cache: dict[str, tuple[str, float]] = {}  # auth_token -> (access, expires_at)
        self._lock = asyncio.Lock()

    async def send(self, auth_token: str, live_chat_id: str, text: str) -> tuple[bool, str | None]:
        record = self._store.lookup(auth_token)
        if not record:
            return False, "not logged in (run: ytchat login)"

        text = (text or "").strip()
        if not text:
            return False, "empty message"
        if len(text) > MAX_LEN:
            text = text[:MAX_LEN]

        access = await self._access_token(auth_token, record["refresh_token"])
        if not access:
            return False, "session expired, log in again"

        ok, err = await oauth.insert_chat_message(self._client, access, live_chat_id, text)
        if not ok and err and "401" in err:
            # Access token may have just expired; drop cache and retry once.
            self._cache.pop(auth_token, None)
            access = await self._access_token(auth_token, record["refresh_token"])
            if access:
                ok, err = await oauth.insert_chat_message(self._client, access, live_chat_id, text)
        return ok, err

    async def _access_token(self, auth_token: str, refresh_token: str) -> str | None:
        now = time.monotonic()
        cached = self._cache.get(auth_token)
        if cached and cached[1] > now:
            return cached[0]
        async with self._lock:
            cached = self._cache.get(auth_token)
            if cached and cached[1] > now:
                return cached[0]
            try:
                access = await oauth.refresh_access_token(
                    self._client, self._cid, self._csecret, refresh_token)
            except oauth.OAuthError as e:
                log.info("refresh failed: %r", e)
                return None
            # Google access tokens last ~1h; cache a bit short to be safe.
            self._cache[auth_token] = (access, now + 3000)
            return access
