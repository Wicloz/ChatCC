import oauth
import pytest
import sender as sender_mod
from credentials import CredentialStore
from _util import run


def _store_with_login(tmp_path, refresh="RT"):
    store = CredentialStore(tmp_path / "c.json")
    token = store.issue(refresh, oauth.SCOPE, "Alice")
    return store, token


def _mk(store):
    return sender_mod.Sender(None, "cid", "sec", store)


def test_send_requires_login(tmp_path):
    snd = _mk(CredentialStore(tmp_path / "c.json"))
    ok, err = run(snd.send("bogus-token", "CHAT", "hi"))
    assert ok is False and "not logged in" in err


def test_send_rejects_empty(tmp_path):
    store, token = _store_with_login(tmp_path)
    ok, err = run(_mk(store).send(token, "CHAT", "   "))
    assert ok is False and "empty" in err


def test_send_success(monkeypatch, tmp_path):
    store, token = _store_with_login(tmp_path)
    calls = []

    async def fake_refresh(c, cid, csec, rt):
        return "ACCESS"

    async def fake_insert(c, access, chat, text):
        calls.append((access, chat, text))
        return True, None

    monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(oauth, "insert_chat_message", fake_insert)

    ok, err = run(_mk(store).send(token, "CHAT", "hello"))
    assert ok and err is None
    assert calls == [("ACCESS", "CHAT", "hello")]


def test_send_caches_access_token(monkeypatch, tmp_path):
    store, token = _store_with_login(tmp_path)
    refreshes = []

    async def fake_refresh(c, cid, csec, rt):
        refreshes.append(rt)
        return "ACCESS"

    async def fake_insert(c, a, chat, text):
        return True, None

    monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(oauth, "insert_chat_message", fake_insert)

    snd = _mk(store)
    run(snd.send(token, "CHAT", "a"))
    run(snd.send(token, "CHAT", "b"))
    assert len(refreshes) == 1  # second send reused the cached access token


def test_send_truncates_to_max_len(monkeypatch, tmp_path):
    store, token = _store_with_login(tmp_path)
    sent = []

    async def fake_refresh(*a):
        return "ACCESS"

    async def fake_insert(c, a, chat, text):
        sent.append(text)
        return True, None

    monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(oauth, "insert_chat_message", fake_insert)

    run(_mk(store).send(token, "CHAT", "x" * 500))
    assert len(sent[0]) == sender_mod.MAX_LEN


def test_send_refresh_failure(monkeypatch, tmp_path):
    store, token = _store_with_login(tmp_path)

    async def fake_refresh(*a):
        raise oauth.OAuthError("invalid_grant")

    monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)
    ok, err = run(_mk(store).send(token, "CHAT", "hi"))
    assert ok is False and "log in again" in err


def test_send_propagates_insert_error(monkeypatch, tmp_path):
    store, token = _store_with_login(tmp_path)

    async def fake_refresh(*a):
        return "ACCESS"

    async def fake_insert(c, a, chat, text):
        return False, "The live chat is no longer live."

    monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(oauth, "insert_chat_message", fake_insert)
    ok, err = run(_mk(store).send(token, "CHAT", "hi"))
    assert ok is False and "no longer live" in err
