"""Synthetic encrypted-store lifecycle and Windows DPAPI tests."""

from datetime import datetime, timedelta, timezone
import os
import sqlite3

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from themis_shield.dpapi import DPAPIProtector, NullProtector, ProtectionError
from themis_shield.vault import (
    InMemoryVault, Vault, VaultConflict, VaultError, VaultExpired,
)


NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
TOKEN = "⟦P:abcdef⟧"


@pytest.fixture(params=["memory", "encrypted"])
def store(request, tmp_path):
    clock = [NOW]
    if request.param == "memory":
        vault = InMemoryVault(clock=lambda: clock[0])
    else:
        vault = Vault(
            tmp_path / "vault.db", protector=NullProtector(), insecure_test_only=True,
            clock=lambda: clock[0],
        )
    yield vault, clock
    vault.close()


def test_mapping_interface_scope_and_immutability(store):
    vault, clock = store
    vault.put("one", TOKEN, "Mara Lindahl")
    vault.put("one", TOKEN, "Mara Lindahl")
    assert vault.get("one", TOKEN) == "Mara Lindahl"
    assert vault.get("two", TOKEN) is None
    assert vault.mapping("one") == {TOKEN: "Mara Lindahl"}
    snapshot = vault.mapping("one")
    snapshot.clear()
    assert vault.get("one", TOKEN) == "Mara Lindahl"
    with pytest.raises(VaultConflict):
        vault.put("one", TOKEN, "Ravi Example")


def test_wipe_and_wipe_all(store):
    vault, clock = store
    vault.put("one", TOKEN, "Mara Lindahl")
    vault.put("two", TOKEN, "Ravi Example")
    vault.wipe("one")
    assert vault.get("one", TOKEN) is None
    assert vault.mapping("one") == {}
    assert vault.get("two", TOKEN) == "Ravi Example"
    vault.wipe_all()
    assert vault.mapping("two") == {}


def test_expiry_boundary_and_reads_refresh_idle_time(store):
    vault, clock = store
    vault.put("one", TOKEN, "Mara Lindahl")
    clock[0] += timedelta(hours=3)
    assert vault.get("one", TOKEN) == "Mara Lindahl"
    assert vault.purge_expired(clock[0] + timedelta(hours=3)) == ()
    clock[0] += timedelta(hours=4)
    assert vault.get("one", TOKEN) is None
    with pytest.raises(VaultExpired):
        vault.put("one", TOKEN, "Mara Lindahl")
    assert vault.purge_expired(clock[0]) == ("one",)
    vault.put("one", TOKEN, "Ravi Example")
    assert vault.get("one", TOKEN) == "Ravi Example"


def test_encrypted_values_never_written_as_sqlite_plaintext(tmp_path):
    path = tmp_path / "vault.db"
    with Vault(path, protector=NullProtector(), insecure_test_only=True) as vault:
        vault.put("one", TOKEN, "Mara Lindahl")
        vault.put("one", "⟦I:123456⟧", "S1234567")
        assert vault._db.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert vault._db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    for file in tmp_path.iterdir():
        raw = file.read_bytes()
        assert b"Mara Lindahl" not in raw
        assert b"S1234567" not in raw
    with Vault(path, protector=NullProtector(), insecure_test_only=True) as reopened:
        assert reopened.get("one", TOKEN) == "Mara Lindahl"


def test_aad_prevents_swapping_conversation_ciphertext(tmp_path):
    with Vault(tmp_path / "vault.db", protector=NullProtector(), insecure_test_only=True) as vault:
        vault.put("one", TOKEN, "Mara Lindahl")
        vault.put("two", TOKEN, "Ravi Example")
        nonce, ciphertext = vault._db.execute(
            "SELECT nonce, ciphertext FROM mappings WHERE conversation_id='one'"
        ).fetchone()
        vault._db.execute(
            "UPDATE mappings SET nonce=?, ciphertext=? WHERE conversation_id='two'",
            (nonce, ciphertext),
        )
        with pytest.raises(VaultError, match="authentication"):
            vault.get("two", TOKEN)


def test_crypto_shred_removes_active_key_before_rows(tmp_path):
    path = tmp_path / "vault.db"
    with Vault(path, protector=NullProtector(), insecure_test_only=True) as vault:
        vault.put("one", TOKEN, "Mara Lindahl")
        nonce, ciphertext = vault._db.execute(
            "SELECT nonce, ciphertext FROM mappings"
        ).fetchone()
        statements = []
        vault._db.set_trace_callback(statements.append)
        vault.wipe("one")
        vault._db.set_trace_callback(None)
        key_delete = next(
            index for index, statement in enumerate(statements)
            if statement.startswith("DELETE FROM conversation_keys")
        )
        row_delete = next(
            index for index, statement in enumerate(statements)
            if statement.startswith("DELETE FROM mappings")
        )
        assert key_delete < row_delete
        assert any(statement == "COMMIT" for statement in statements[key_delete:row_delete])
        assert vault._db.execute("SELECT * FROM conversation_keys").fetchall() == []
        assert vault.get("one", TOKEN) is None
        # A new key under the same conversation identifier cannot decrypt an
        # old retained ciphertext. This is not a historic-backup guarantee.
        vault.put("one", TOKEN, "Ravi Example")
        new_key = vault._key("one", datetime.now(timezone.utc), create=False)
        with pytest.raises(InvalidTag):
            from themis_shield.vault import _aad
            AESGCM(new_key).decrypt(nonce, ciphertext, _aad("one", "original", TOKEN))
    with Vault(path, protector=NullProtector(), insecure_test_only=True) as reopened:
        assert reopened.get("one", TOKEN) == "Ravi Example"


def test_orphan_rows_after_key_deletion_are_not_readable(tmp_path):
    path = tmp_path / "vault.db"
    with Vault(path, protector=NullProtector(), insecure_test_only=True) as vault:
        vault.put("one", TOKEN, "Mara Lindahl")
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM conversation_keys")
    connection.commit()
    connection.close()
    with Vault(path, protector=NullProtector(), insecure_test_only=True) as vault:
        assert vault.mapping("one") == {}
        assert vault._db.execute("SELECT * FROM mappings").fetchall() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows CurrentUser DPAPI only")
def test_windows_dpapi_round_trip():
    protector = DPAPIProtector()
    value = b"synthetic test bytes, not a production key"
    protected = protector.protect(value)
    assert protected != value
    assert protector.unprotect(protected) == value


@pytest.mark.skipif(os.name == "nt", reason="non-Windows failure contract")
def test_dpapi_fails_clearly_off_windows(tmp_path):
    with pytest.raises(ProtectionError, match="Windows"):
        DPAPIProtector()
    with pytest.raises(ProtectionError, match="Windows"):
        Vault(tmp_path / "vault.db")
    assert not (tmp_path / "vault.db").exists()


def test_null_protector_is_refused_without_the_explicit_test_flag(tmp_path):
    with pytest.raises(VaultError, match="insecure_test_only"):
        Vault(tmp_path / "vault.db", protector=NullProtector())
    assert not (tmp_path / "vault.db").exists()


def test_vault_refuses_to_start_off_windows_without_a_real_protector(tmp_path):
    from themis_shield.protectors import build_protector
    with pytest.raises(ProtectionError, match="passphrase"):
        build_protector({}, platform="posix")
    with pytest.raises(ProtectionError, match="Windows"):
        build_protector({"protector": "dpapi"}, platform="posix")
    with pytest.raises(ProtectionError, match="insecure_test_only"):
        build_protector({"protector": "null"}, platform="posix")
    assert isinstance(
        build_protector({"protector": "null", "insecure_test_only": True}, platform="posix"),
        NullProtector,
    )
    with pytest.raises(ProtectionError):
        build_protector({"protector": "rot13"})


def test_passphrase_protector_round_trip(tmp_path, monkeypatch):
    from themis_shield.protectors import PassphraseProtector, SALT_BYTES, build_protector
    protector = PassphraseProtector("correct horse battery staple")
    value = b"synthetic wrapping key bytes, not a production key"
    first, second = protector.protect(value), protector.protect(value)
    assert first != second and value not in first  # fresh salt and nonce each time
    assert protector.unprotect(first) == value
    salt = first[4:4 + SALT_BYTES]
    assert len(salt) == 16
    with pytest.raises(ProtectionError):
        PassphraseProtector("a different passphrase").unprotect(first)
    with pytest.raises(ProtectionError):
        PassphraseProtector("short")
    monkeypatch.setenv("SYNTHETIC_VAULT_PASSPHRASE", "correct horse battery staple")
    configured = build_protector(
        {"protector": "passphrase", "passphrase_env": "SYNTHETIC_VAULT_PASSPHRASE"}, platform="posix",
    )
    path = tmp_path / "vault.db"
    with Vault(path, protector=configured, clock=lambda: NOW) as vault:
        vault.put("conversation-1", TOKEN, "Synthetic Person")
    reopened_protector = PassphraseProtector("correct horse battery staple")
    with Vault(path, protector=reopened_protector, clock=lambda: NOW) as reopened:
        assert reopened.mapping("conversation-1") == {TOKEN: "Synthetic Person"}
    with Vault(path, protector=PassphraseProtector("wrong passphrase value"), clock=lambda: NOW) as wrong:
        with pytest.raises(ProtectionError):
            wrong.mapping("conversation-1")
