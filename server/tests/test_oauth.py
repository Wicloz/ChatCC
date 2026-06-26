import oauth
import pytest
from _util import FakeHTTP, FakeResponse, run


def test_request_device_code_parses():
    http = FakeHTTP([FakeResponse(json_data={
        "device_code": "DC", "user_code": "ABCD-EFGH",
        "verification_url": "https://www.google.com/device",
        "expires_in": 1800, "interval": 5,
    })])
    info = run(oauth.request_device_code(http, "cid"))
    assert info["device_code"] == "DC"
    assert info["user_code"] == "ABCD-EFGH"
    assert info["verification_url"] == "https://www.google.com/device"
    assert info["interval"] == 5
    # scope and client_id are sent in the POST body.
    _, url, data, _ = http.calls[0]
    assert url == oauth.DEVICE_CODE_URL
    assert data["client_id"] == "cid" and data["scope"] == oauth.SCOPE


def test_request_device_code_accepts_verification_uri_alias():
    http = FakeHTTP([FakeResponse(json_data={
        "device_code": "DC", "user_code": "X",
        "verification_uri": "https://example/device", "expires_in": 1, "interval": 1,
    })])
    info = run(oauth.request_device_code(http, "cid"))
    assert info["verification_url"] == "https://example/device"


def test_request_device_code_error():
    http = FakeHTTP([FakeResponse(status_code=400, text="bad client")])
    with pytest.raises(oauth.OAuthError):
        run(oauth.request_device_code(http, "cid"))


@pytest.mark.parametrize("status_code,body,expected", [
    (200, {"access_token": "AT", "refresh_token": "RT"}, oauth.SUCCESS),
    (428, {"error": "authorization_pending"}, oauth.PENDING),
    (403, {"error": "slow_down"}, oauth.SLOW_DOWN),
    (403, {"error": "access_denied"}, oauth.DENIED),
    (400, {"error": "expired_token"}, oauth.EXPIRED),
    (400, {"error": "something_else"}, oauth.ERROR),
])
def test_poll_token_status_mapping(status_code, body, expected):
    http = FakeHTTP([FakeResponse(status_code=status_code, json_data=body)])
    status, data = run(oauth.poll_token(http, "cid", "secret", "DC"))
    assert status == expected
    _, url, sent, _ = http.calls[0]
    assert url == oauth.TOKEN_URL
    assert sent["grant_type"] == oauth.DEVICE_GRANT
    assert sent["client_secret"] == "secret"


def test_refresh_access_token():
    http = FakeHTTP([FakeResponse(json_data={"access_token": "fresh"})])
    token = run(oauth.refresh_access_token(http, "cid", "secret", "RT"))
    assert token == "fresh"
    _, _, sent, _ = http.calls[0]
    assert sent["grant_type"] == oauth.REFRESH_GRANT and sent["refresh_token"] == "RT"


def test_refresh_access_token_error():
    http = FakeHTTP([FakeResponse(status_code=400, text="invalid_grant")])
    with pytest.raises(oauth.OAuthError):
        run(oauth.refresh_access_token(http, "cid", "secret", "RT"))


def test_fetch_channel_title():
    http = FakeHTTP([FakeResponse(json_data={"items": [{"snippet": {"title": "My Channel"}}]})])
    assert run(oauth.fetch_channel_title(http, "AT")) == "My Channel"
    _, url, params, headers = http.calls[0]
    assert headers["Authorization"] == "Bearer AT"


def test_fetch_channel_title_handles_empty():
    http = FakeHTTP([FakeResponse(json_data={"items": []})])
    assert run(oauth.fetch_channel_title(http, "AT")) is None
