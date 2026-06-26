"""Google OAuth 2.0 limited-input device flow (server-side only).

The tablet never talks to Google; this module makes every Google call on the
server's behalf, using the client id/secret that stay in the server's env.
"""

import httpx

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_GRANT = "refresh_token"

# Writing live chat requires this scope; an API key cannot write.
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

# Poll outcomes the caller distinguishes.
PENDING = "authorization_pending"
SLOW_DOWN = "slow_down"
DENIED = "access_denied"
EXPIRED = "expired_token"
SUCCESS = "success"
ERROR = "error"


class OAuthError(Exception):
    pass


async def request_device_code(client: httpx.AsyncClient, client_id: str, scope: str = SCOPE) -> dict:
    """Begin the flow. Returns device_code/user_code/verification_url/expires_in/interval."""
    r = await client.post(DEVICE_CODE_URL, data={"client_id": client_id, "scope": scope})
    if r.status_code != 200:
        raise OAuthError(f"device/code HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    return {
        "device_code": d["device_code"],
        "user_code": d["user_code"],
        # Google returns verification_url; tolerate the spec's verification_uri too.
        "verification_url": d.get("verification_url") or d.get("verification_uri"),
        "expires_in": int(d.get("expires_in", 1800)),
        "interval": int(d.get("interval", 5)),
    }


async def poll_token(client: httpx.AsyncClient, client_id: str, client_secret: str,
                     device_code: str) -> tuple[str, dict]:
    """Poll once. Returns (status, data); status is SUCCESS/PENDING/SLOW_DOWN/DENIED/EXPIRED/ERROR."""
    r = await client.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "device_code": device_code,
        "grant_type": DEVICE_GRANT,
    })
    data = r.json()
    if r.status_code == 200:
        return SUCCESS, data
    err = data.get("error")
    if err in (PENDING, SLOW_DOWN, DENIED, EXPIRED):
        return err, data
    return ERROR, data


async def refresh_access_token(client: httpx.AsyncClient, client_id: str, client_secret: str,
                               refresh_token: str) -> str:
    """Exchange a stored refresh token for a fresh access token (used when sending)."""
    r = await client.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": REFRESH_GRANT,
    })
    if r.status_code != 200:
        raise OAuthError(f"refresh HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["access_token"]


async def fetch_channel_title(client: httpx.AsyncClient, access_token: str) -> str | None:
    """Best-effort: the authenticated account's channel title, for a friendly label."""
    try:
        r = await client.get(
            CHANNELS_URL,
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0].get("snippet", {}).get("title")
    except httpx.HTTPError:
        pass
    return None
