"""Per-user default locations. All of them can be overridden in configuration.

Windows: ``%LOCALAPPDATA%/themis-shield``; elsewhere ``~/.local/share/themis-shield``.
Model weights and the llama.cpp engine default to ``models/`` and ``engine/``
below that directory.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "themis-shield"


def app_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_DIR_NAME
    # Portable fallback for non-Windows hosts and development.
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def default_models_dir() -> Path:
    return app_data_dir() / "models"


def default_engine_path() -> Path:
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    return app_data_dir() / "engine" / name


def default_tracer_dir() -> Path:
    return default_models_dir() / "pii-tracer"
