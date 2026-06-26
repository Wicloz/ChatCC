"""YouTube helpers: parse a video id and resolve its active live-chat id."""

import re

import httpx

VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/live/|/embed/|/shorts/|/watch\?.*v=)([A-Za-z0-9_-]{11})")


def extract_video_id(s: str) -> str | None:
    """Accept a full YouTube URL or a bare 11-char id."""
    s = (s or "").strip()
    m = _ID_RE.search(s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    return None


async def resolve_live_chat_id(
    client: httpx.AsyncClient, api_key: str, video_id: str
) -> tuple[str | None, str | None]:
    """Return (live_chat_id, None) or (None, error_message)."""
    try:
        r = await client.get(
            VIDEOS_URL,
            params={"part": "liveStreamingDetails", "id": video_id, "key": api_key},
        )
    except httpx.HTTPError as e:
        return None, f"network error resolving video: {e!r}"

    if r.status_code != 200:
        return None, f"videos.list HTTP {r.status_code}"

    items = r.json().get("items", [])
    if not items:
        return None, "no such video (or not accessible)"

    details = items[0].get("liveStreamingDetails", {})
    chat_id = details.get("activeLiveChatId")
    if not chat_id:
        return None, "video is not currently live, or live chat is disabled"
    return chat_id, None
