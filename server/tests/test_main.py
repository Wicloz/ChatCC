import main
import oauth
from credentials import CredentialStore
from _util import FakeRequest, FakeWS, run


# -- URL injection -----------------------------------------------------------

def test_base_url_honours_forwarded_headers():
    req = FakeRequest(headers={"x-forwarded-proto": "https", "x-forwarded-host": "srv"})
    assert main._server_base_url(req) == "https://srv"


def test_base_url_fallback_to_request():
    req = FakeRequest(base_url="http://testserver/")
    assert main._server_base_url(req) == "http://testserver"


def test_serve_lua_injects_url():
    req = FakeRequest(headers={"x-forwarded-proto": "https", "x-forwarded-host": "srv"})
    body = main._serve_lua("client.lua", req).body.decode("utf-8")
    assert 'local SERVER = "https://srv"' in body


def test_source_files_keep_template_token():
    # The raw file on disk must still contain the quoted placeholder so the
    # server can inject at runtime.
    raw = (main.LUA_DIR / "install.lua").read_text(encoding="utf-8")
    assert '"{{SERVER}}"' in raw


# -- WebSocket validation (no network) ---------------------------------------

def test_ws_rejects_missing_video():
    ws = FakeWS(query={})
    run(main.ws_chat(ws))
    assert ws.accepted and ws.closed
    assert any(f.startswith("S") and "error" in f for f in ws.sent)


def test_ws_rejects_bad_video():
    ws = FakeWS(query={"v": "this is not an id"})
    run(main.ws_chat(ws))
    assert ws.accepted and ws.closed
    assert any(f.startswith("S") and "error" in f for f in ws.sent)


def test_ws_login_rejected_when_unconfigured(monkeypatch):
    # No GOOGLE_CLIENT_ID/SECRET configured -> login disabled.
    monkeypatch.setattr(main, "CLIENT_ID", None)
    monkeypatch.setattr(main, "CLIENT_SECRET", None)
    ws = FakeWS()
    run(main.ws_login(ws))
    assert ws.accepted and ws.closed
    assert any(f.startswith("S") and "not configured" in f for f in ws.sent)


class _Src:
    live_chat_id = "CHAT"


def test_post_message_without_sender(monkeypatch):
    monkeypatch.setattr(main, "sender", None)
    ws = FakeWS()
    run(main._post_message(ws, _Src(), "tok", "hi"))
    assert any(f.startswith("S") and "not configured" in f for f in ws.sent)


def test_post_message_reports_error(monkeypatch):
    class FakeSender:
        async def send(self, token, chat, text):
            return False, "boom"
    monkeypatch.setattr(main, "sender", FakeSender())
    ws = FakeWS()
    run(main._post_message(ws, _Src(), "tok", "hi"))
    assert any(f.startswith("S") and "boom" in f for f in ws.sent)


def test_logout_endpoint_revokes(monkeypatch, tmp_path):
    store = CredentialStore(tmp_path / "c.json")
    token = store.issue("R", "s", "Alice", "CH1")
    monkeypatch.setattr(main, "store", store, raising=False)
    monkeypatch.setattr(main, "oauth_http", None, raising=False)

    async def fake_revoke(client, t):
        return True
    monkeypatch.setattr(oauth, "revoke_token", fake_revoke)

    req = FakeRequest(json_body={"token": token, "all": False})
    result = run(main.logout_endpoint(req))
    assert result == {"ok": True, "revoked": 1}
    assert store.lookup(token) is None


def test_post_message_success_is_silent(monkeypatch):
    sent = []

    class FakeSender:
        async def send(self, token, chat, text):
            sent.append((token, chat, text))
            return True, None
    monkeypatch.setattr(main, "sender", FakeSender())
    ws = FakeWS()
    run(main._post_message(ws, _Src(), "tok", "hi"))
    assert ws.sent == []                      # success echoes via the stream
    assert sent == [("tok", "CHAT", "hi")]
