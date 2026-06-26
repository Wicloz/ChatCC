import json

import protocol


def test_message_frame_shape():
    frame = protocol.message("id1", "alice", "hello", "moderator", "textMessageEvent")
    assert frame[0] == "M"
    payload = json.loads(frame[1:])
    assert payload == {
        "id": "id1", "a": "alice", "m": "hello",
        "r": "moderator", "t": "textMessageEvent",
    }


def test_status_frame_shape():
    frame = protocol.status("error", "boom")
    assert frame[0] == "S"
    assert json.loads(frame[1:]) == {"s": "error", "m": "boom"}


def test_status_default_message():
    assert json.loads(protocol.status("live")[1:]) == {"s": "live", "m": ""}


def test_unicode_preserved_not_escaped():
    frame = protocol.message("i", "nørdy", "héllo 😀", "user", "textMessageEvent")
    # ensure_ascii=False keeps real characters, and it still round-trips.
    assert "😀" in frame
    assert json.loads(frame[1:])["m"] == "héllo 😀"


def test_compact_separators():
    assert ", " not in protocol.status("live")
