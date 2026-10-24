"""Owned local-model catalogue, scheduling and calibration.

No import-time probing, engine launch, file creation or endpoint contact.
Use build_runtime() once per application and close it on application shutdown.
"""

from .catalogue import Catalogue, ModelEntry
from .scheduler import Scheduler
from .settings import RuntimeSettings, build_runtime

__all__ = [
    "Catalogue", "ModelEntry", "Scheduler", "RuntimeSettings", "build_runtime",
]
