"""Deployment profiles: shipped defaults plus an external, deployment-owned directory.

Organisations adopt themis-shield as an upstream dependency and keep their own
deployment layer outside this repository. Every deployment-specific piece is a
named JSON profile of one *kind*:

- ``markings``    security-marking schemes and their routing policy
- ``institution`` allow-list facts (the organisation's own names, sites,
                  role mailboxes, public numbers and codes; never personal data)
- ``catalogue``   local model catalogue patches and entries
- ``cloud``       the cloud endpoint (base URL, model, env-var *name* for the key)

A profile ``<kind>/<name>.json`` is looked up first in the external profiles
directory (``[profiles] dir`` in the gate configuration, or the
``THEMIS_PROFILES_DIR`` environment variable), then in the profiles shipped
inside this package. Shipped profiles are only defaults; an external file with
the same name replaces a shipped one.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

from ..inference.json_output import strict_loads

KINDS = ("markings", "institution", "catalogue", "cloud")
PROFILES_DIR_ENV = "THEMIS_PROFILES_DIR"
MAX_PROFILE_BYTES = 1024 * 1024
_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_SHIPPED = Path(__file__).resolve().parent


def shipped_dir() -> Path:
    return _SHIPPED


def external_dir(profiles_dir: str | os.PathLike | None = None) -> Path | None:
    """Resolve the external profiles directory (argument, then environment)."""
    value = profiles_dir if profiles_dir not in (None, "") else os.environ.get(PROFILES_DIR_ENV)
    if not value:
        return None
    return Path(value).expanduser()


def _check(kind: str, name: str) -> None:
    if kind not in KINDS:
        raise ValueError("unknown profile kind")
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name.endswith(".json"):
        raise ValueError("invalid profile name")


def profile_path(kind: str, name: str, profiles_dir: str | os.PathLike | None = None) -> Path:
    """Find ``<kind>/<name>.json``: external directory first, then shipped."""
    _check(kind, name)
    candidates = []
    external = external_dir(profiles_dir)
    if external is not None:
        candidates.append(external / kind / f"{name}.json")
    candidates.append(_SHIPPED / kind / f"{name}.json")
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(f"{kind} profile not found: {name}")


def load_profile_json(kind: str, name: str, profiles_dir: str | os.PathLike | None = None) -> Any:
    """Load and strictly parse one profile document."""
    path = profile_path(kind, name, profiles_dir)
    if path.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError("profile exceeds bound")
    return strict_loads(path.read_bytes())


def available_profiles(kind: str, profiles_dir: str | os.PathLike | None = None) -> list[str]:
    """Names visible for a kind (external and shipped, de-duplicated)."""
    if kind not in KINDS:
        raise ValueError("unknown profile kind")
    names = set()
    roots = [_SHIPPED / kind]
    external = external_dir(profiles_dir)
    if external is not None:
        roots.insert(0, external / kind)
    for root in roots:
        if root.is_dir():
            names.update(
                path.stem for path in root.glob("*.json") if _NAME.fullmatch(path.stem)
            )
    return sorted(names)
