"""Per-user credential store.

Maps an opaque bearer auth-token (held by the tablet) to a Google refresh token
(held only here). The auth-token is stored *hashed*, so a leak of this file
cannot be replayed as a valid token. The refresh token is the sensitive value;
the file is written 0600 and is gitignored / excluded from the image.

TODO (hardening, per design notes): encrypt the refresh token at rest with a
key from the environment. The store is deliberately a small class so that can
be slotted in without touching callers.
"""

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class CredentialStore:
    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._records = json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records), encoding="utf-8")
        os.replace(tmp, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX perms

    def issue(self, refresh_token: str, scope: str, account: str | None = None,
              channel_id: str | None = None) -> str:
        """Store a refresh token and return a fresh bearer auth-token for the tablet."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._records[_hash(token)] = {
                "refresh_token": refresh_token,
                "scope": scope,
                "account": account,
                "channel_id": channel_id,
                "created_at": int(time.time()),
            }
            self._save()
        return token

    def lookup(self, token: str) -> dict | None:
        if not token:
            return None
        with self._lock:
            return self._records.get(_hash(token))

    def revoke(self, token: str) -> bool:
        return self.pop(token) is not None

    def pop(self, token: str) -> dict | None:
        """Remove and return the record for one auth-token (this device)."""
        if not token:
            return None
        with self._lock:
            record = self._records.pop(_hash(token), None)
            if record is not None:
                self._save()
            return record

    def pop_account(self, channel_id: str) -> list[dict]:
        """Remove and return every record for a Google account (all its devices)."""
        if not channel_id:
            return []
        with self._lock:
            hashes = [h for h, r in self._records.items() if r.get("channel_id") == channel_id]
            removed = [self._records.pop(h) for h in hashes]
            if removed:
                self._save()
            return removed

    def __len__(self) -> int:
        return len(self._records)
