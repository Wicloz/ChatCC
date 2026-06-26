import httpx
import pytest

import youtube
from _util import FakeGetClient, FakeResponse, run


@pytest.mark.parametrize("raw,expected", [
    ("https://www.youtube.com/watch?v=WofY4HsVAPk", "WofY4HsVAPk"),
    ("https://youtu.be/WofY4HsVAPk", "WofY4HsVAPk"),
    ("https://www.youtube.com/live/WofY4HsVAPk", "WofY4HsVAPk"),
    ("https://www.youtube.com/embed/WofY4HsVAPk", "WofY4HsVAPk"),
    ("https://www.youtube.com/watch?list=x&v=WofY4HsVAPk&t=1", "WofY4HsVAPk"),
    ("WofY4HsVAPk", "WofY4HsVAPk"),
    ("  WofY4HsVAPk  ", "WofY4HsVAPk"),
])
def test_extract_video_id_ok(raw, expected):
    assert youtube.extract_video_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "not a url", "https://example.com", "short", None])
def test_extract_video_id_bad(raw):
    assert youtube.extract_video_id(raw) is None


def test_resolve_ok():
    resp = FakeResponse(json_data={"items": [
        {"liveStreamingDetails": {"activeLiveChatId": "CHAT123"}}
    ]})
    client = FakeGetClient(response=resp)
    chat_id, err = run(youtube.resolve_live_chat_id(client, "k", "vid"))
    assert err is None
    assert chat_id == "CHAT123"
    # API key is passed as a query param, not in the path/URL string.
    _, params = client.calls[0]
    assert params["key"] == "k" and params["id"] == "vid"


def test_resolve_no_items():
    client = FakeGetClient(response=FakeResponse(json_data={"items": []}))
    chat_id, err = run(youtube.resolve_live_chat_id(client, "k", "vid"))
    assert chat_id is None and "no such video" in err


def test_resolve_not_live():
    resp = FakeResponse(json_data={"items": [{"liveStreamingDetails": {}}]})
    client = FakeGetClient(response=resp)
    chat_id, err = run(youtube.resolve_live_chat_id(client, "k", "vid"))
    assert chat_id is None and "not currently live" in err


def test_resolve_http_error():
    client = FakeGetClient(response=FakeResponse(status_code=403, text="quota"))
    chat_id, err = run(youtube.resolve_live_chat_id(client, "k", "vid"))
    assert chat_id is None and "403" in err


def test_resolve_network_error():
    client = FakeGetClient(exc=httpx.ConnectError("down"))
    chat_id, err = run(youtube.resolve_live_chat_id(client, "k", "vid"))
    assert chat_id is None and "network error" in err
