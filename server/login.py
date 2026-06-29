"""Orchestrates one device-login session and reports progress over a WebSocket.

Sends these frames to the tablet:
    D  device prompt   {url, code, expires}   -> show the user_code + URL
    S  status          {s, m}                 -> "error" on failure
    A  login ok        {token, account}       -> bearer auth-token to save
"""

import asyncio
import logging
import time

import oauth
import protocol

log = logging.getLogger("chatcc.login")


async def perform_device_login(ws, client, client_id, client_secret, store) -> None:
    """Run the full device flow, pushing progress to `ws`. Returns when done."""
    info = await oauth.request_device_code(client, client_id)
    await ws.send_text(protocol.device_prompt(
        info["verification_url"], info["user_code"], info["expires_in"]))

    interval = max(info["interval"], 1)
    deadline = time.monotonic() + info["expires_in"]

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        status, data = await oauth.poll_token(client, client_id, client_secret, info["device_code"])

        if status == oauth.PENDING:
            continue
        if status == oauth.SLOW_DOWN:
            interval += 5
            continue
        if status == oauth.SUCCESS:
            refresh = data.get("refresh_token")
            if not refresh:
                # Happens if the user already granted before and Google omits it.
                await ws.send_text(protocol.status("error", "no refresh token returned; revoke access and retry"))
                return
            # Granular consent: the user can uncheck the permission on the consent
            # screen. Without the youtube scope we can't send, so reject clearly
            # now rather than failing confusingly on the first message.
            scope = data.get("scope", "")
            if oauth.SCOPE not in scope.split():
                log.info("login missing required scope; granted=%r", scope)
                await ws.send_text(protocol.status(
                    "error",
                    "send permission not granted. Log in again and keep the "
                    "YouTube permission checked."))
                return
            channel_id, account = await oauth.fetch_channel_info(client, data.get("access_token"))
            token = store.issue(refresh, scope, account, channel_id)
            log.info("login success for account=%s", account)
            await ws.send_text(protocol.login_ok(token, account or ""))
            return
        if status == oauth.DENIED:
            await ws.send_text(protocol.status("error", "access denied"))
            return
        if status == oauth.EXPIRED:
            break
        await ws.send_text(protocol.status("error", data.get("error", "login failed")))
        return

    await ws.send_text(protocol.status("error", "code expired, please try again"))
