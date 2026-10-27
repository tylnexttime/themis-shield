"""Value-free startup checks for ``themis serve``.

Every refusal carries a fixed reason code and a one-line explanation. Neither
ever contains a configuration value, a path, a passphrase, a token or an
exception message: the codes and texts below are the only things printed. The
exit code stays non-zero.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
import tomllib
from typing import Any, Mapping

from .dpapi import ProtectionError

REASONS = {
    "CONFIG_MISSING": (
        "No configuration file at the given path. Copy config/rehearsal.example.toml (or the minimal "
        "config/themis.example.toml) and pass --config <path> or set THEMIS_CONFIG."
    ),
    "CONFIG_UNREADABLE": "The configuration file is not valid TOML.",
    "CONFIG_INCOMPLETE": (
        "The configuration lacks a required setting: [gate] with synthetic_only and requester_identity."
    ),
    "CONFIG_INVALID": (
        "A configuration setting is unknown or has the wrong type; compare it with config/themis.example.toml."
    ),
    "CONFIG_REFERENCE_MISSING": (
        "A profile, catalogue or runtime settings file named in the configuration was not found "
        "(see `themis profiles`)."
    ),
    "SYNTHETIC_ONLY_REQUIRED": (
        "[gate] synthetic_only must be true: this preview runs on synthetic data only."
    ),
    "OPERATOR_IS_REQUESTER": (
        "[gate] operator_identity must differ from requester_identity: nobody approves their own job."
    ),
    "GATE_TOKEN_MISSING": (
        "The local gate token is not provisioned. Run `themis serve --provision-token` once, "
        "or start with `themis serve --open`."
    ),
    "PORT_IN_USE": (
        "The port is already in use on 127.0.0.1. Stop the other process or choose another --port "
        "(--open always uses 8742)."
    ),
    "VAULT_PROTECTOR_UNAVAILABLE": (
        "The vault key protector is unavailable: off Windows set [vault] protector = \"passphrase\" "
        "and put a passphrase of at least 12 characters in the environment variable named by passphrase_env."
    ),
    "VAULT_KEY_REJECTED": (
        "The vault key could not be unwrapped: wrong passphrase, another user's vault, or a damaged vault file."
    ),
    "TRACER_EXTRA_MISSING": (
        "PII-Tracer needs the tracer extra (torch, transformers): pip install -e \".[tracer]\"."
    ),
    "TRACER_WEIGHTS_MISSING": (
        "PII-Tracer weights were not found: put them in <app data>/models/pii-tracer or set "
        "[detection] tracer_model_directory."
    ),
    "ENGINE_MISSING": (
        "llama-server was not found where the runtime expects it (default <app data>/engine); "
        "set engine_path in the runtime settings file."
    ),
    "MODEL_MISSING": (
        "A finder model file named in the catalogue is missing from the models directory "
        "(see `themis runtime list`)."
    ),
    "CANARY_FAILED": (
        "The detector canary failed: a detector returned less than its known answer on the fixed synthetic "
        "canary documents (wrong weights, a tokenizer mismatch or an inference-engine template change). "
        "Check the named detector, or set [canary] enabled = false only for diagnosis."
    ),
    "STARTUP_FAILED": "Startup failed for another reason; check the configuration and dependencies.",
}

FINDER_ROLES = ("finder-fast", "finder-thorough")


class StartupRefused(Exception):
    """A value-free startup refusal: ``code`` is a key of :data:`REASONS`."""

    def __init__(self, code: str, detail: str | None = None):
        if code not in REASONS:
            raise ValueError("unknown startup reason")
        super().__init__(code)
        self.code = code
        # Only fixed, non-personal detail is allowed (an exception class name,
        # a catalogue model file name, or a canary detector and expectation
        # name), never a value from the configuration.
        self.detail = detail

    @property
    def message(self) -> str:
        suffix = f" ({self.detail})" if self.detail else ""
        return f"Themis startup refused [{self.code}]: {REASONS[self.code]}{suffix}"


def check_config(path: Path) -> dict:
    """Read the configuration and check the settings every start needs."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise StartupRefused("CONFIG_MISSING")
    try:
        with path.open("rb") as stream:
            config = tomllib.load(stream)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise StartupRefused("CONFIG_UNREADABLE") from None
    except OSError:
        raise StartupRefused("CONFIG_MISSING") from None
    gate = config.get("gate")
    if not isinstance(gate, dict) or "synthetic_only" not in gate or not gate.get("requester_identity"):
        raise StartupRefused("CONFIG_INCOMPLETE")
    if gate.get("synthetic_only") is not True:
        raise StartupRefused("SYNTHETIC_ONLY_REQUIRED")
    if gate.get("operator_identity") == gate["requester_identity"]:
        raise StartupRefused("OPERATOR_IS_REQUESTER")
    return config


def check_token(path: Path, reader) -> str:
    try:
        return reader(path)
    except (OSError, ValueError):
        raise StartupRefused("GATE_TOKEN_MISSING") from None


def check_port(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))
    except OSError:
        raise StartupRefused("PORT_IN_USE") from None
    finally:
        probe.close()


def classify(error: BaseException) -> StartupRefused:
    """Map a build failure to a reason code without reading its message."""
    from .vault import VaultError

    if isinstance(error, StartupRefused):
        return error
    if isinstance(error, ProtectionError):
        # PassphraseMismatch is a ProtectionError raised when a key exists but does not unwrap.
        from .protectors import PassphraseMismatch
        return StartupRefused(
            "VAULT_KEY_REJECTED" if isinstance(error, PassphraseMismatch) else "VAULT_PROTECTOR_UNAVAILABLE"
        )
    if isinstance(error, VaultError):
        return StartupRefused("VAULT_KEY_REJECTED")
    if isinstance(error, FileNotFoundError):
        return StartupRefused("CONFIG_REFERENCE_MISSING")
    if isinstance(error, KeyError):
        return StartupRefused("CONFIG_INCOMPLETE")
    if isinstance(error, (ValueError, TypeError)):
        return StartupRefused("CONFIG_INVALID")
    return StartupRefused("STARTUP_FAILED", type(error).__name__)


def build(config_path: Path, builder):
    """Run the router builder, turning any failure into a value-free refusal."""
    try:
        return builder(config_path)
    except Exception as error:
        raise classify(error) from None


def check_vault(router: Any) -> None:
    """Unwrap the vault's user key once, so a wrong passphrase stops startup."""
    verify = getattr(router.vault, "verify_key", None)
    if verify is None:
        return
    try:
        verify()
    except Exception as error:
        raise classify(error) from None


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_models(router: Any, *, module_present=_module_present) -> None:
    """Refuse when the local detection stack cannot run (the gate would block every request)."""
    if not (module_present("torch") and module_present("transformers")):
        raise StartupRefused("TRACER_EXTRA_MISSING")
    tracer = Path(router.targets.tracer_model_directory)
    if not tracer.is_dir() or not any(tracer.iterdir()):
        raise StartupRefused("TRACER_WEIGHTS_MISSING")
    settings = router.runtime_settings
    runtime = router.runtime
    mapping: Mapping[str, str] = getattr(runtime, "role_models", None) or {}
    for role in FINDER_ROLES:
        model_id = mapping.get(role)
        if model_id is None:
            continue
        entry = runtime.catalogue.get(model_id)
        if entry.runtime != "llamacpp":
            continue
        if not Path(settings.engine_for(entry)).is_file():
            raise StartupRefused("ENGINE_MISSING")
        if not (Path(settings.models_dir) / entry.file).is_file():
            raise StartupRefused("MODEL_MISSING", Path(entry.file).name)


def check_canary(router: Any):
    """Run the detector canary before serving (``[canary] enabled`` and ``at_startup``, both on by default).

    A failure refuses to start, naming the first failing detector and its fixed expectation name. Returns the
    canary result, or None when the canary is off or deferred (it then runs before the first cloud-bound work).
    """
    monitor = getattr(router, "canary", None)
    if monitor is None or not monitor.settings.at_startup:
        return None
    result = monitor.startup()
    if not result.passed:
        first = result.failures[0]
        detail = first.detector + ": " + ", ".join(first.failed)
        if first.error_code:
            detail += f" [{first.error_code}]"
        raise StartupRefused("CANARY_FAILED", detail)
    return result
