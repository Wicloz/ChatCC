"""Logout: revoke a tablet's credential, optionally for the whole account.

`all_devices=False` -> just the calling device's token.
`all_devices=True`  -> every device logged into the same Google account
                       (used when a computer is lost/stolen: run it from any
                       still-logged-in device to kill the stolen one too).

In both cases we also revoke the refresh token(s) at Google (best-effort), so
they're dead even if our store is later compromised.
"""

import logging

import oauth

log = logging.getLogger("chatcc.logout")


async def logout(client, store, token: str, all_devices: bool) -> int:
    """Revoke credentials. Returns how many device records were removed."""
    record = store.lookup(token)
    if not record:
        return 0  # unknown/already-revoked token: idempotent no-op

    if all_devices and record.get("channel_id"):
        removed = store.pop_account(record["channel_id"])
    else:
        popped = store.pop(token)
        removed = [popped] if popped else []

    for rec in removed:
        refresh = rec.get("refresh_token")
        if refresh:
            await oauth.revoke_token(client, refresh)  # best-effort

    log.info("logout removed %d device(s) (all=%s)", len(removed), all_devices)
    return len(removed)
