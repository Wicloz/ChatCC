import json

import chat_source
from chat_source import ChatSource
from _util import FakeStreamResponse, run


def _src():
    # No real client/key needed for the parsing/fan-out units.
    return ChatSource(client=None, api_key="k", live_chat_id="CHAT")


def _msgs(frames):
    return [json.loads(f[1:]) for f in frames if f[:1] == "M"]


def _item(mid, name, text, **flags):
    ad = {"displayName": name}
    ad.update(flags)
    return {
        "id": mid,
        "snippet": {"type": "textMessageEvent", "displayMessage": text},
        "authorDetails": ad,
    }


def test_role_mapping():
    src = _src()
    q = src.add_subscriber()
    src._handle_item(_item("1", "o", "x", isChatOwner=True))
    src._handle_item(_item("2", "m", "x", isChatModerator=True))
    src._handle_item(_item("3", "s", "x", isChatSponsor=True))
    src._handle_item(_item("4", "v", "x", isVerified=True))
    src._handle_item(_item("5", "u", "x"))
    roles = [m["r"] for m in _msgs(_drain(q))]
    assert roles == ["owner", "moderator", "member", "verified", "user"]


def test_dedup_by_id():
    src = _src()
    q = src.add_subscriber()
    src._handle_item(_item("dup", "a", "first"))
    src._handle_item(_item("dup", "a", "again"))
    assert len(_msgs(_drain(q))) == 1


def test_consume_parses_split_stream_and_tracks_token():
    src = _src()
    q = src.add_subscriber()
    docs = [
        {"items": [_item("a", "u", "hi"), _item("b", "u", "there")], "nextPageToken": "T1"},
        {"items": [_item("c", "u", "more")], "nextPageToken": "T2"},
    ]
    full = json.dumps(docs)
    # Feed the stream in tiny chunks to exercise the incremental buffer.
    chunks = [full[i:i + 7] for i in range(0, len(full), 7)]
    resp = FakeStreamResponse(chunks=chunks)
    token = run(src._consume(resp, None))
    assert token == "T2"
    texts = [m["m"] for m in _msgs(_drain(q))]
    assert texts == ["hi", "there", "more"]


def test_backlog_replayed_to_late_subscriber():
    src = _src()
    src._handle_item(_item("1", "a", "old1"))
    src._handle_item(_item("2", "a", "old2"))
    q = src.add_subscriber()  # joins after the messages
    frames = _drain(q)
    assert frames[0][0] == "S"  # current state first
    assert [m["m"] for m in _msgs(frames)] == ["old1", "old2"]


def test_slow_client_drops_without_blocking_others(monkeypatch):
    monkeypatch.setattr(chat_source, "QUEUE_MAX", 2)
    src = _src()
    slow = src.add_subscriber()   # will overflow
    fast = src.add_subscriber()
    _drain(fast)                  # fast keeps up (drained)
    for i in range(10):
        src._handle_item(_item(str(i), "a", f"m{i}"))
        _drain(fast)              # fast drains continuously
    # slow never drained -> bounded, no exception raised, fast got everything.
    assert slow.qsize() <= 2
    # No assertion error means fan-out didn't blow up on the full queue.


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out
