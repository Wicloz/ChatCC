import asyncio
import json

import pytest

import chat_source
import youtube
from hub import ChatHub
from _util import run


@pytest.fixture
def fake_reader(monkeypatch):
    """Make sources resolve+stream synthetic data, no network."""
    async def fake_resolve(client, key, video_id):
        return ("CHAT_" + video_id, None)

    async def fake_run(self):
        self._set_state("live")
        i = 0
        try:
            while True:
                i += 1
                self._handle_item({
                    "id": f"{self.live_chat_id}-{i}",
                    "snippet": {"type": "textMessageEvent", "displayMessage": f"m{i}"},
                    "authorDetails": {"displayName": "a"},
                })
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(youtube, "resolve_live_chat_id", fake_resolve)
    monkeypatch.setattr(chat_source.ChatSource, "_run", fake_run)


def _drain_msgs(q):
    out = []
    while not q.empty():
        f = q.get_nowait()
        if f[:1] == "M":
            out.append(json.loads(f[1:]))
    return out


def test_same_video_shares_source_and_fans_out(fake_reader):
    async def go():
        h = ChatHub("k")
        s1, q1, e1 = await h.acquire("vid1")
        s2, q2, e2 = await h.acquire("vid1")
        assert e1 is None and e2 is None
        assert s1 is s2
        assert s1.subscriber_count == 2
        await asyncio.sleep(0.2)
        assert _drain_msgs(q1) and _drain_msgs(q2)  # both clients fed
        await h.aclose()
    run(go())


def test_different_videos_get_separate_sources(fake_reader):
    async def go():
        h = ChatHub("k")
        s1, _, _ = await h.acquire("vid1")
        s2, _, _ = await h.acquire("vid2")
        assert s1 is not s2
        assert len(h._sources) == 2
        await h.aclose()
    run(go())


def test_source_torn_down_when_last_client_leaves(fake_reader):
    async def go():
        h = ChatHub("k")
        s, q1, _ = await h.acquire("vid1")
        _, q2, _ = await h.acquire("vid1")
        await h.release(s, q1)
        assert s.subscriber_count == 1
        assert "CHAT_vid1" in h._sources
        assert s._task is not None
        await h.release(s, q2)            # last one leaves
        assert "CHAT_vid1" not in h._sources
        assert s._task is None            # reader cancelled
        await h.aclose()
    run(go())


def test_acquire_propagates_resolve_error(monkeypatch):
    async def bad_resolve(client, key, video_id):
        return (None, "not currently live")
    monkeypatch.setattr(youtube, "resolve_live_chat_id", bad_resolve)

    async def go():
        h = ChatHub("k")
        source, queue, err = await h.acquire("vid1")
        assert source is None and queue is None
        assert err == "not currently live"
        assert len(h._sources) == 0
        await h.aclose()
    run(go())
