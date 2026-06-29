import asyncio
import json

import login
import oauth
import pytest
from credentials import CredentialStore
from _util import FakeWS, run


def _frames(ws):
    return [(f[0], json.loads(f[1:])) for f in ws.sent]


@pytest.fixture
def no_sleep(monkeypatch):
    async def fast(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", fast)


def _patch_device(monkeypatch, interval=1, expires=100):
    async def req(client, client_id, scope=oauth.SCOPE):
        return {"device_code": "DC", "user_code": "WXYZ",
                "verification_url": "https://g/device",
                "expires_in": expires, "interval": interval}
    monkeypatch.setattr(oauth, "request_device_code", req)


def test_login_success_stores_refresh_and_returns_token(monkeypatch, no_sleep, tmp_path):
    _patch_device(monkeypatch)
    polls = [(oauth.PENDING, {}),
             (oauth.SUCCESS, {"refresh_token": "RT", "access_token": "AT", "scope": oauth.SCOPE})]

    async def poll(*a):
        return polls.pop(0)
    monkeypatch.setattr(oauth, "poll_token", poll)

    async def info(*a):
        return "CHID", "Alice"
    monkeypatch.setattr(oauth, "fetch_channel_info", info)

    store = CredentialStore(tmp_path / "c.json")
    ws = FakeWS()
    run(login.perform_device_login(ws, None, "cid", "sec", store))

    frames = _frames(ws)
    assert frames[0][0] == "D" and frames[0][1]["code"] == "WXYZ"
    op, payload = frames[-1]
    assert op == "A" and payload["account"] == "Alice"
    rec = store.lookup(payload["token"])
    assert rec["refresh_token"] == "RT" and rec["channel_id"] == "CHID"


def test_login_slow_down_then_success(monkeypatch, no_sleep, tmp_path):
    _patch_device(monkeypatch)
    polls = [(oauth.SLOW_DOWN, {}),
             (oauth.SUCCESS, {"refresh_token": "RT", "access_token": "AT", "scope": oauth.SCOPE})]

    async def poll(*a):
        return polls.pop(0)
    monkeypatch.setattr(oauth, "poll_token", poll)

    async def info(*a):
        return None, None
    monkeypatch.setattr(oauth, "fetch_channel_info", info)

    store = CredentialStore(tmp_path / "c.json")
    ws = FakeWS()
    run(login.perform_device_login(ws, None, "cid", "sec", store))
    assert _frames(ws)[-1][0] == "A"
    assert len(store) == 1


def test_login_denied(monkeypatch, no_sleep, tmp_path):
    _patch_device(monkeypatch)

    async def poll(*a):
        return (oauth.DENIED, {"error": "access_denied"})
    monkeypatch.setattr(oauth, "poll_token", poll)

    store = CredentialStore(tmp_path / "c.json")
    ws = FakeWS()
    run(login.perform_device_login(ws, None, "cid", "sec", store))
    frames = _frames(ws)
    assert frames[0][0] == "D"
    assert frames[-1][0] == "S" and frames[-1][1]["s"] == "error"
    assert len(store) == 0


def test_login_without_required_scope_is_error(monkeypatch, no_sleep, tmp_path):
    _patch_device(monkeypatch)

    async def poll(*a):
        # User unchecked the permission: granted some other scope, not youtube.
        return (oauth.SUCCESS, {"refresh_token": "RT", "access_token": "AT",
                                "scope": "https://www.googleapis.com/auth/userinfo.email"})
    monkeypatch.setattr(oauth, "poll_token", poll)

    store = CredentialStore(tmp_path / "c.json")
    ws = FakeWS()
    run(login.perform_device_login(ws, None, "cid", "sec", store))
    frames = _frames(ws)
    assert frames[-1][0] == "S" and frames[-1][1]["s"] == "error"
    assert "permission" in frames[-1][1]["m"]
    assert len(store) == 0


def test_login_success_without_refresh_token_is_error(monkeypatch, no_sleep, tmp_path):
    _patch_device(monkeypatch)

    async def poll(*a):
        return (oauth.SUCCESS, {"access_token": "AT"})  # no refresh_token
    monkeypatch.setattr(oauth, "poll_token", poll)

    store = CredentialStore(tmp_path / "c.json")
    ws = FakeWS()
    run(login.perform_device_login(ws, None, "cid", "sec", store))
    assert _frames(ws)[-1][1]["s"] == "error"
    assert len(store) == 0


def test_login_expires(monkeypatch, no_sleep, tmp_path):
    _patch_device(monkeypatch, expires=0)  # deadline already passed

    async def poll(*a):
        return (oauth.PENDING, {})
    monkeypatch.setattr(oauth, "poll_token", poll)

    store = CredentialStore(tmp_path / "c.json")
    ws = FakeWS()
    run(login.perform_device_login(ws, None, "cid", "sec", store))
    frames = _frames(ws)
    assert frames[0][0] == "D"
    assert frames[-1][0] == "S" and "expired" in frames[-1][1]["m"]
