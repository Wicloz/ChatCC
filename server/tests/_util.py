"""Shared helpers and fakes for the test suite (mirrors LiveCC's style:
lightweight fakes + a run() helper instead of pytest-asyncio)."""

import asyncio


def run(coro):
    return asyncio.run(coro)


class Clock:
    """Controllable monotonic clock for deterministic rate-limit tests."""

    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeResponse:
    """Stand-in for a non-streaming httpx response (.get)."""

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.text = text

    def json(self):
        return self._json


class FakeGetClient:
    """Stand-in for httpx.AsyncClient exposing .get()."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        if self._exc is not None:
            raise self._exc
        return self._response


class FakeStreamResponse:
    """Stand-in for the streaming response consumed by ChatSource._consume."""

    def __init__(self, status_code=200, chunks=(), body=b""):
        self.status_code = status_code
        self._chunks = list(chunks)
        self._body = body

    async def aiter_text(self):
        for c in self._chunks:
            yield c

    async def aread(self):
        return self._body


class FakeHTTP:
    """Stand-in for httpx.AsyncClient supporting .post()/.get(); returns queued
    FakeResponses in order."""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []

    def queue(self, *responses):
        self._responses.extend(responses)

    async def post(self, url, data=None, headers=None):
        self.calls.append(("POST", url, data, headers))
        return self._next()

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        return self._next()

    def _next(self):
        if not self._responses:
            raise AssertionError("FakeHTTP: no more responses queued")
        return self._responses.pop(0)


class FakeURL:
    def __init__(self, base):
        self._base = base

    def __str__(self):
        return self._base


class FakeRequest:
    def __init__(self, headers=None, base_url="http://testserver/", json_body=None):
        self.headers = headers or {}
        self.base_url = FakeURL(base_url)
        self._json = json_body

    async def json(self):
        return self._json


class FakeWS:
    """Minimal WebSocket stand-in for unit-testing the ws handler."""

    def __init__(self, query=None, recv_exc=None):
        self.query_params = query or {}
        self.sent = []
        self.accepted = False
        self.closed = False
        self._recv_exc = recv_exc

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def receive_text(self):
        from fastapi import WebSocketDisconnect
        raise self._recv_exc or WebSocketDisconnect()

    async def close(self, *args, **kwargs):
        self.closed = True
