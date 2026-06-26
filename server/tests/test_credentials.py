import json

from credentials import CredentialStore, _hash


def test_issue_and_lookup(tmp_path):
    store = CredentialStore(tmp_path / "creds.json")
    token = store.issue("REFRESH", "scope", "Alice")
    rec = store.lookup(token)
    assert rec["refresh_token"] == "REFRESH"
    assert rec["account"] == "Alice"
    assert rec["scope"] == "scope"


def test_lookup_unknown_returns_none(tmp_path):
    store = CredentialStore(tmp_path / "creds.json")
    assert store.lookup("nope") is None
    assert store.lookup("") is None
    assert store.lookup(None) is None


def test_token_stored_hashed_not_plaintext(tmp_path):
    path = tmp_path / "creds.json"
    store = CredentialStore(path)
    token = store.issue("REFRESH", "scope")
    raw = path.read_text(encoding="utf-8")
    assert token not in raw                 # bearer token never written verbatim
    assert _hash(token) in raw              # only its hash is the key
    on_disk = json.loads(raw)
    assert _hash(token) in on_disk


def test_persists_across_instances(tmp_path):
    path = tmp_path / "creds.json"
    token = CredentialStore(path).issue("REFRESH", "scope", "Bob")
    reloaded = CredentialStore(path)
    assert reloaded.lookup(token)["account"] == "Bob"


def test_revoke(tmp_path):
    store = CredentialStore(tmp_path / "creds.json")
    token = store.issue("REFRESH", "scope")
    assert store.revoke(token) is True
    assert store.lookup(token) is None
    assert store.revoke(token) is False     # idempotent


def test_unique_tokens(tmp_path):
    store = CredentialStore(tmp_path / "creds.json")
    t1 = store.issue("R1", "s")
    t2 = store.issue("R2", "s")
    assert t1 != t2
    assert len(store) == 2
