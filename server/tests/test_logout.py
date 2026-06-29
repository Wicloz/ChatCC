import logout
import oauth
from credentials import CredentialStore
from _util import run


def _store(tmp_path):
    return CredentialStore(tmp_path / "c.json")


def test_logout_single_device(monkeypatch, tmp_path):
    store = _store(tmp_path)
    token = store.issue("R", "s", "Alice", "CH1")
    revoked = []

    async def fake_revoke(client, t):
        revoked.append(t)
        return True
    monkeypatch.setattr(oauth, "revoke_token", fake_revoke)

    n = run(logout.logout(None, store, token, all_devices=False))
    assert n == 1
    assert store.lookup(token) is None
    assert revoked == ["R"]               # refresh token revoked at Google too


def test_logout_all_devices_for_account(monkeypatch, tmp_path):
    store = _store(tmp_path)
    a = store.issue("R1", "s", "Alice", "CH1")
    b = store.issue("R2", "s", "Alice", "CH1")   # Alice's 2nd device (e.g. stolen)
    c = store.issue("R3", "s", "Bob", "CH2")
    revoked = []

    async def fake_revoke(client, t):
        revoked.append(t)
        return True
    monkeypatch.setattr(oauth, "revoke_token", fake_revoke)

    # Run "logout all" from device A; it must also kill device B.
    n = run(logout.logout(None, store, a, all_devices=True))
    assert n == 2
    assert store.lookup(a) is None and store.lookup(b) is None
    assert store.lookup(c) is not None           # other account untouched
    assert set(revoked) == {"R1", "R2"}


def test_logout_unknown_token_is_noop(monkeypatch, tmp_path):
    store = _store(tmp_path)

    async def boom(client, t):
        raise AssertionError("should not revoke for unknown token")
    monkeypatch.setattr(oauth, "revoke_token", boom)

    assert run(logout.logout(None, store, "nope", all_devices=True)) == 0


def test_logout_all_without_channel_id_falls_back_to_single(monkeypatch, tmp_path):
    store = _store(tmp_path)
    token = store.issue("R", "s", "Alice", None)  # legacy/missing channel id
    revoked = []

    async def fake_revoke(client, t):
        revoked.append(t)
        return True
    monkeypatch.setattr(oauth, "revoke_token", fake_revoke)

    n = run(logout.logout(None, store, token, all_devices=True))
    assert n == 1                                  # only this device
    assert revoked == ["R"]
