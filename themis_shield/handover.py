"""Handover mode: deterministic manifest and conservative package verifier.


A package contains manifest.json and explicitly listed UTF-8 text files under
inputs/. The local vault is NEVER part of the package. No imports or calls to
the vault, credential store or transcript reader occur here.

Verification rejects extra files, symlinks, hard links, escaping paths, binary
content and recognised vault material. vault_material optionally supplies known
local vault byte sequences which must not occur in exported inputs. It is held
in memory only and never serialized or logged.

Structural verification is not semantic de-identification: arbitrarily renamed
plaintext original values cannot be identified without provenance and a real
PII pipeline. Those release controls remain stubs. Verify a private, immutable
snapshot and recheck immediately before send; this pathname-based verifier is
not a protection against concurrent hostile filesystem mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType
from typing import Mapping

from .detectors.markings import MarkingClass, marking_policy
from .routing import Route
from .types import Label


MAXIMUM_FILE_BYTES = 4 * 1024 * 1024
_ID = re.compile(r"JOB-[0-9]{4}-[0-9]{4}")
_HASH = re.compile(r"[0-9a-f]{64}")
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}
_FORBIDDEN_NAMES = ("vault", "credential", "replacement", "restoration", "secret")
_VAULT_SIGNATURE = re.compile(
    rb'"(?:wrapped_data_key|ciphertext|replacement_map|surface_bindings|'
    rb'original_value)"\s*:'
)


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _input_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("invalid package path")
    parts = value.split("/")
    if len(parts) < 2 or parts[0] != "inputs":
        raise ValueError("package inputs must be under inputs/")
    for part in parts:
        if (
            not _COMPONENT.fullmatch(part)
            or part in {".", ".."}
            or part.endswith(".")
            or part.split(".", 1)[0].casefold() in _RESERVED
            or any(word in part.casefold() for word in _FORBIDDEN_NAMES)
        ):
            raise ValueError("unsafe package path")
    path = PurePosixPath(value)
    if path.suffix.casefold() not in {".txt", ".md", ".json", ".csv"}:
        raise ValueError("unsupported package file type")
    return path


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _input_path(self.path)
        if not isinstance(self.sha256, str) or not _HASH.fullmatch(self.sha256):
            raise ValueError("invalid file hash")


@dataclass(frozen=True, slots=True)
class PeerCheck:
    checked: bool = False
    checked_by_role: str | None = None
    checked_utc: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.checked) is not bool:
            raise ValueError("invalid peer check")
        if self.checked:
            if (
                not isinstance(self.checked_by_role, str)
                or not _VERSION.fullmatch(self.checked_by_role)
                or self.checked_utc is None
            ):
                raise ValueError("checked peer record requires role and time")
            _utc_text(self.checked_utc)
        elif self.checked_by_role is not None or self.checked_utc is not None:
            raise ValueError("unchecked peer record must have no reviewer")


@dataclass(frozen=True, slots=True)
class HandoverManifest:
    job_id: str
    created_utc: datetime
    marking_class: MarkingClass
    route: Route
    pii_counts: Mapping[str, int]
    sensitive_categories_present: bool
    files: tuple[ManifestFile, ...]
    problem_summary_deid: str = field(repr=False)
    peer_check: PeerCheck
    gate_version: str
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not _ID.fullmatch(self.job_id):
            raise ValueError("invalid job identifier")
        _utc_text(self.created_utc)
        if (
            not isinstance(self.marking_class, MarkingClass)
            or not isinstance(self.route, Route)
            or type(self.sensitive_categories_present) is not bool
            or not isinstance(self.peer_check, PeerCheck)
        ):
            raise ValueError("invalid manifest metadata")
        if not isinstance(self.problem_summary_deid, str):
            raise ValueError("summary must be de-identified text")
        for version in (self.gate_version, self.policy_version):
            if not isinstance(version, str) or not _VERSION.fullmatch(version):
                raise ValueError("invalid version alias")
        counts = dict(self.pii_counts)
        if any(
            key not in Label.__members__ or type(count) is not int or count < 0
            for key, count in counts.items()
        ):
            raise ValueError("invalid PII counts")
        files = tuple(self.files)
        if not files or any(not isinstance(item, ManifestFile) for item in files):
            raise ValueError("manifest requires typed input files")
        paths = [item.path.casefold() for item in files]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate package path")
        object.__setattr__(self, "pii_counts", MappingProxyType(counts))
        object.__setattr__(
            self, "files", tuple(sorted(files, key=lambda item: item.path))
        )


def canonical_manifest(manifest: HandoverManifest) -> bytes:
    """Canonical UTF-8 JSON; filenames sorted, keys sorted, UTC normalized.

    No BOM, trailing newline, insignificant whitespace or non-finite numbers.
    The manifest hash is not embedded in its own input.
    """

    peer = manifest.peer_check
    value = {
        "job_id": manifest.job_id,
        "created_utc": _utc_text(manifest.created_utc),
        "marking_class": manifest.marking_class.name,
        "route": manifest.route.value,
        "pii_counts": dict(manifest.pii_counts),
        "sensitive_categories_present": manifest.sensitive_categories_present,
        "files": [
            {"path": item.path, "sha256": item.sha256} for item in manifest.files
        ],
        "problem_summary_deid": manifest.problem_summary_deid,
        "peer_check": {
            "checked": peer.checked,
            "checked_by_role": peer.checked_by_role,
            "checked_utc": (
                _utc_text(peer.checked_utc) if peer.checked_utc is not None else None
            ),
        },
        "gate_version": manifest.gate_version,
        "policy_version": manifest.policy_version,
    }
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def manifest_hash(manifest: HandoverManifest) -> str:
    return hashlib.sha256(canonical_manifest(manifest)).hexdigest()


def _read_regular(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAXIMUM_FILE_BYTES
    ):
        raise ValueError("package requires bounded, unlinked regular files")
    with path.open("rb") as stream:
        raw = stream.read(MAXIMUM_FILE_BYTES + 1)
    if len(raw) > MAXIMUM_FILE_BYTES:
        raise ValueError("package file exceeds bound")
    return raw


def _check_text(raw: bytes, vault_material: tuple[bytes, ...]) -> None:
    if b"\x00" in raw or raw.startswith(b"SQLite format 3"):
        raise ValueError("binary or vault material is forbidden")
    try:
        raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("package input must be UTF-8 text") from None
    if _VAULT_SIGNATURE.search(raw) or any(value in raw for value in vault_material):
        raise ValueError("vault material is forbidden")


def verify_package(
    package_dir: Path,
    manifest: HandoverManifest,
    *,
    vault_material: tuple[bytes, ...] = (),
    confirmed_manifest_hash: str | None = None,
) -> str:
    """Verify an entire cloud handover snapshot and return its manifest hash.

    Reject LOCAL_ONLY/HYBRID and confirmation-tier markings and above; cloud
    authorisation for the confirmation tier is not implemented by this skeleton.
    Missing, extra, externally linked or changed files are errors, not warnings.
    The caller must still authenticate and match the central approval.
    """

    if manifest.route != Route.CLOUD:
        raise ValueError("package is not on the cloud route")
    if marking_policy().is_local_only(manifest.marking_class):
        raise ValueError("marking is not authorised for cloud handover")
    if marking_policy().needs_confirm(manifest.marking_class) and (
        confirmed_manifest_hash != manifest_hash(manifest)
    ):
        raise ValueError("sensitive handover requires exact-manifest confirmation")
    if any(not isinstance(value, bytes) or not value for value in vault_material):
        raise ValueError("known vault material must be non-empty byte sequences")
    root = Path(package_dir)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("package root must be a real directory")
    root = root.resolve(strict=True)
    expected = {"manifest.json"} | {item.path for item in manifest.files}
    expected_dirs = {
        str(parent)
        for item in manifest.files
        for parent in PurePosixPath(item.path).parents
        if str(parent) != "."
    }
    found = set()
    for directory, directories, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in directories:
            child = base / name
            relative = child.relative_to(root).as_posix()
            if child.is_symlink() or relative not in expected_dirs:
                raise ValueError("unexpected or linked package directory")
        for name in filenames:
            child = base / name
            relative = child.relative_to(root).as_posix()
            if relative not in expected or child.is_symlink():
                raise ValueError("unexpected or linked package file")
            if not child.resolve(strict=True).is_relative_to(root):
                raise ValueError("package file escapes root")
            metadata = child.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("package file has external linkage")
            found.add(relative)
    if found != expected:
        raise ValueError("package inventory does not match manifest")
    manifest_bytes = _read_regular(root / "manifest.json")
    if manifest_bytes != canonical_manifest(manifest):
        raise ValueError("package manifest does not match canonical manifest")
    _check_text(manifest_bytes, vault_material)
    for item in manifest.files:
        raw = _read_regular(root.joinpath(*PurePosixPath(item.path).parts))
        _check_text(raw, vault_material)
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError("package input hash mismatch")
    return manifest_hash(manifest)


def verify_snapshot(manifest, files, *, confirmed_manifest_hash=None):
    """Verify an immutable in-memory export; no filesystem or vault access."""
    if manifest.route != Route.CLOUD or marking_policy().is_local_only(manifest.marking_class):
        raise ValueError("no cloud handover for this route or marking")
    if marking_policy().needs_confirm(manifest.marking_class):
        if confirmed_manifest_hash != manifest_hash(manifest):
            raise ValueError("sensitive handover requires exact confirmation")
    if set(files) != {item.path for item in manifest.files}:
        raise ValueError("snapshot inventory mismatch")
    _check_text(canonical_manifest(manifest), ())
    for item in manifest.files:
        raw = files[item.path]
        if not isinstance(raw, bytes) or len(raw) > MAXIMUM_FILE_BYTES:
            raise ValueError("invalid snapshot bytes")
        _check_text(raw, ())
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError("snapshot hash mismatch")
    return manifest_hash(manifest)
