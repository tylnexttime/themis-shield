"""Business timezone for approval deadlines, reporting and day-first display.

Configured once at startup (``[register] business_timezone`` in the gate
configuration, or the ``THEMIS_BUSINESS_TIMEZONE`` environment variable).
The default is UTC so that nothing about a deployment's location is assumed.
"""

from __future__ import annotations

import os
from threading import Lock
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "UTC"

_lock = Lock()
_zone: ZoneInfo | None = None


def business_timezone() -> ZoneInfo:
    """Return the configured business timezone (IANA name)."""
    global _zone
    with _lock:
        if _zone is None:
            _zone = ZoneInfo(os.environ.get("THEMIS_BUSINESS_TIMEZONE") or DEFAULT_TIMEZONE)
        return _zone


def set_business_timezone(name: str | None) -> ZoneInfo:
    """Set the process-wide business timezone; ``None`` restores the default lookup."""
    global _zone
    with _lock:
        _zone = ZoneInfo(name) if name else None
    return business_timezone()
