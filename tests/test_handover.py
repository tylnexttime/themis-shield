"""Synthetic deterministic-hash and whole-package verification tests."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os

import pytest

from themis_shield.detectors.markings import MarkingClass
from themis_shield.handover import (
    HandoverManifest,
    ManifestFile,
    PeerCheck,
    canonical_manifest,
    manifest_hash,
    verify_package,
)
from themis_shield.routing import Route

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def _manifest(files, **changes):
    return replace(
        HandoverManifest(
            "JOB-2026-0001", NOW, MarkingClass.INTERNAL, Route.CLOUD,
            {"PERSON": 2, "EMAIL": 1}, False, tuple(files),
            "Synthetic de-identified problem summary.", PeerCheck(),
            "1.2-skeleton", "1.2-proposed",
        ),
        **changes,
    )


def _package(tmp_path, raw=b"Synthetic clean input with placeholder: \xe2\x9f\xa6P:7f3a\xe2\x9f\xa7"):
    root = tmp_path / "package"
    (root / "inputs").mkdir(parents=True)
    path = root / "inputs" / "clean.txt"
    path.write_bytes(raw)
    manifest = _manifest([
        ManifestFile("inputs/clean.txt", hashlib.sha256(raw).hexdigest())
    ])
    (root / "manifest.json").write_bytes(canonical_manifest(manifest))
    return root, manifest


def test_canonical_hash_ignores_mapping_and_file_order():
    first = ManifestFile("inputs/a.txt", "a" * 64)
    second = ManifestFile("inputs/b.txt", "b" * 64)
    left = _manifest([first, second])
    right = _manifest(
        [second, first], pii_counts={"EMAIL": 1, "PERSON": 2},
        created_utc=NOW.astimezone(timezone(timedelta(hours=10))),
    )
    assert canonical_manifest(left) == canonical_manifest(right)
    assert manifest_hash(left) == manifest_hash(right)
    assert manifest_hash(left) == hashlib.sha256(canonical_manifest(left)).hexdigest()


def test_manifest_copies_mutable_inputs():
    counts = {"PERSON": 1}
    manifest = _manifest([ManifestFile("inputs/a.txt", "a" * 64)], pii_counts=counts)
    before = manifest_hash(manifest)
    counts["PERSON"] = 100
    assert manifest_hash(manifest) == before
    with pytest.raises(TypeError):
        manifest.pii_counts["PERSON"] = 100


def test_scope_metadata_changes_hash():
    manifest = _manifest([ManifestFile("inputs/a.txt", "a" * 64)])
    assert manifest_hash(replace(
        manifest, problem_summary_deid="Different synthetic summary."
    )) != manifest_hash(manifest)


def test_valid_package_returns_manifest_hash(tmp_path):
    root, manifest = _package(tmp_path)
    assert verify_package(root, manifest) == manifest_hash(manifest)


def test_changed_file_is_rejected(tmp_path):
    root, manifest = _package(tmp_path)
    (root / "inputs" / "clean.txt").write_text("Changed synthetic input")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_package(root, manifest)


def test_changed_disk_manifest_is_rejected(tmp_path):
    root, manifest = _package(tmp_path)
    (root / "manifest.json").write_bytes(canonical_manifest(manifest) + b"\n")
    with pytest.raises(ValueError, match="canonical manifest"):
        verify_package(root, manifest)


def test_missing_file_is_rejected(tmp_path):
    root, manifest = _package(tmp_path)
    (root / "inputs" / "clean.txt").unlink()
    with pytest.raises(ValueError, match="inventory"):
        verify_package(root, manifest)


@pytest.mark.parametrize("name", ["vault.db", ".hidden", "extra.txt"])
def test_any_unlisted_file_including_vault_is_rejected(tmp_path, name):
    root, manifest = _package(tmp_path)
    (root / name).write_bytes(b"Synthetic non-package material")
    with pytest.raises(ValueError):
        verify_package(root, manifest)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt", "/outside.txt", "inputs/../outside.txt",
        "inputs\\outside.txt", "C:/outside.txt", "inputs/vault.txt",
        "inputs/clean.db", "inputs/CON.txt", "inputs/a./clean.txt",
        "inputs//clean.txt",
    ],
)
def test_unsafe_or_vault_paths_cannot_enter_manifest(path):
    with pytest.raises(ValueError):
        ManifestFile(path, "a" * 64)


@pytest.mark.parametrize(
    "raw",
    [
        b'SQLite format 3\x00synthetic',
        b'{"wrapped_data_key":"synthetic","ciphertext":"synthetic"}',
        b'{"original_value":"Synthetic Citizen"}',
        b"\xff\xfe",
    ],
)
def test_renamed_vault_and_binary_material_is_rejected(tmp_path, raw):
    root, manifest = _package(tmp_path, raw)
    with pytest.raises(ValueError):
        verify_package(root, manifest)


def test_known_vault_bytes_are_rejected_even_after_renaming(tmp_path):
    raw = b"SYNTHETIC_LOCAL_VAULT_SENTINEL"
    root, manifest = _package(tmp_path, raw)
    with pytest.raises(ValueError, match="vault material"):
        verify_package(root, manifest, vault_material=(raw,))


def test_external_symlink_is_rejected(tmp_path):
    root, manifest = _package(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_bytes((root / "inputs" / "clean.txt").read_bytes())
    path = root / "inputs" / "clean.txt"
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable on this test host")
    with pytest.raises(ValueError):
        verify_package(root, manifest)


def test_external_hardlink_is_rejected(tmp_path):
    root, manifest = _package(tmp_path)
    path = root / "inputs" / "clean.txt"
    try:
        os.link(path, tmp_path / "outside.txt")
    except OSError:
        pytest.skip("hardlink creation unavailable on this test host")
    with pytest.raises(ValueError):
        verify_package(root, manifest)


@pytest.mark.parametrize(
    "changes",
    [
        {"route": Route.LOCAL_ONLY},
        {"route": Route.HYBRID},
        {"marking_class": MarkingClass.SENSITIVE},
        {"marking_class": MarkingClass.RESTRICTED},
        {"marking_class": MarkingClass.SECRET},
        {"marking_class": MarkingClass.TOP_SECRET},
    ],
)
def test_local_or_unauthorised_marking_has_no_cloud_handover(tmp_path, changes):
    root, manifest = _package(tmp_path)
    manifest = replace(manifest, **changes)
    (root / "manifest.json").write_bytes(canonical_manifest(manifest))
    with pytest.raises(ValueError):
        verify_package(root, manifest)


def test_optional_peer_check_is_hash_bound():
    manifest = _manifest([ManifestFile("inputs/a.txt", "a" * 64)])
    checked = replace(
        manifest,
        peer_check=PeerCheck(True, "PEER_REVIEWER", NOW),
    )
    assert manifest_hash(checked) != manifest_hash(manifest)


def test_duplicate_casefolded_paths_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        _manifest([
            ManifestFile("inputs/a.txt", "a" * 64),
            ManifestFile("inputs/A.txt", "b" * 64),
        ])
