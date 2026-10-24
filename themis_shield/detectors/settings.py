"""Process-wide detection behaviour version.

``v0.2`` (default) adds the deterministic fixes measured on the v0.2 development set: time-shaped tracer dates are
not dates of birth, trailing punctuation is stripped before allow-list matching, the USI checksum needs a digit, the
organisation's own ABN is allow-listed even when checksum-valid, and rare categorical values in identifier-bearing
tables become QUASI_ID. ``v0.1`` restores the evaluated v0.1 behaviour byte for byte (the negative control).

Set at start-up from ``[detection] version`` (or the evaluation runner's ``--detection-version``).
"""

from __future__ import annotations

from threading import Lock


DETECTION_VERSIONS = ("v0.1", "v0.2")
DEFAULT_DETECTION_VERSION = "v0.2"
_lock = Lock()
_version = DEFAULT_DETECTION_VERSION


def configure_detection_version(version: str | None = None) -> str:
    """Set the detection behaviour version; ``None`` restores the default. Returns the active version."""
    global _version
    version = DEFAULT_DETECTION_VERSION if version is None else version
    if version not in DETECTION_VERSIONS:
        raise ValueError("unknown detection version")
    with _lock:
        _version = version
    return _version


def detection_version() -> str:
    return _version


def v02() -> bool:
    """True when the v0.2 detection fixes are on."""
    return _version != "v0.1"
