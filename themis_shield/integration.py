"""Application-owned integration: configuration -> router. No service starts at import time.

The gate configuration is a TOML file that may live anywhere (``--config`` or
``THEMIS_CONFIG``). Deployment-specific pieces (marking scheme and policy,
institution allow-list, local model catalogue, cloud endpoint) are named
profiles resolved through :mod:`themis_shield.profiles`, so a deployment keeps
them in its own external directory without forking this code.
"""

from __future__ import annotations

from dataclasses import fields
import importlib
import os
from pathlib import Path
import tomllib
from typing import Any, Mapping

from .cloud import CloudClient, CloudSettings
from .config import DeploymentProfile, UseCasePolicy
from .detectors import DetectionTargets
from .detectors.llm_finder import configure_finder
from .detectors.rules import set_rule_packs
from .detectors.markings import (
    MarkingPolicy, MarkingProfile, load_marking_profile, set_marking_profile,
)
from .profiles import load_profile_json, profile_path
from .protectors import build_protector
from .register.pilot import PilotAutoApprover
from .register.sqlite_store import SqliteRegister
from .router import Router
from .runtime import RuntimeSettings, build_runtime
from .runtime.calibration import CalibrationStore
from .runtime.catalogue import Catalogue
from .runtime.modes import SequentialFinderClient
from .runtime.resources import read_identity
from .timezone import set_business_timezone
from .vault import Vault

CONFIG_ENV = "THEMIS_CONFIG"
DEFAULT_REGISTER_FACTORY = "themis_shield.integration:register_factory"
_SECRET_WORDS = ("key", "secret", "token", "password", "bearer", "credential")


def register_factory(*, requester_identity=None, path=None):
    return SqliteRegister(
        path, requester_identity=(
            requester_identity
            or os.environ.get("THEMIS_SYNTHETIC_REQUESTER", "synthetic-requester-01")
        ),
    )


def default_config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or "config/local.toml")


def load_config(path) -> dict:
    with Path(path).expanduser().open("rb") as stream:
        return tomllib.load(stream)


def profiles_dir(config: Mapping[str, Any]) -> str | None:
    """The external, deployment-owned profiles directory, if configured."""
    value = (config.get("profiles") or {}).get("dir") or None
    return str(Path(value).expanduser()) if value else None


def configure_markings(config: Mapping[str, Any]) -> MarkingProfile:
    """Load ``[markings] profile`` and apply any policy override; activate it."""
    section = dict(config.get("markings") or {})
    unknown = set(section) - {"profile", "confirm_at", "local_only_at"}
    if unknown:
        raise ValueError("unknown [markings] settings")
    profile = load_marking_profile(section.get("profile") or "generic", profiles_dir(config))
    if "confirm_at" in section or "local_only_at" in section:
        profile = profile.with_policy(MarkingPolicy(
            section.get("confirm_at", profile.policy.confirm_at),
            section.get("local_only_at", profile.policy.local_only_at),
        ))
    return set_marking_profile(profile)


def configure_rule_packs(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Activate ``[detection] rule_packs`` (default: core only)."""
    packs = (config.get("detection") or {}).get("rule_packs")
    return set_rule_packs(packs if packs else None)


def _reject_secrets(table: Mapping[str, Any], where: str) -> None:
    allowed = {item.name for item in fields(CloudSettings)}
    for key in table:
        lowered = key.lower()
        if key not in allowed and any(word in lowered for word in _SECRET_WORDS):
            raise ValueError(f"{where} must not contain credentials; name an env var in api_key_env")


def cloud_settings(config: Mapping[str, Any]) -> CloudSettings:
    """Build cloud settings from ``[cloud]``, optionally layered on a cloud profile."""
    section = dict(config.get("cloud") or {})
    name = section.pop("profile", None)
    values: dict[str, Any] = {}
    if name:
        document = load_profile_json("cloud", name, profiles_dir(config))
        if not isinstance(document, dict):
            raise ValueError("invalid cloud profile")
        document = {key: value for key, value in document.items() if not key.startswith("_")}
        _reject_secrets(document, "cloud profile")
        values.update(document)
    _reject_secrets(section, "[cloud]")
    values.update(section)
    allowed = {item.name for item in fields(CloudSettings)}
    if set(values) - allowed:
        raise ValueError("unknown cloud settings")
    if values.get("capture_dir"):
        values["capture_dir"] = Path(values["capture_dir"]).expanduser()
    return CloudSettings(**values)


def institution_profile(config: Mapping[str, Any]) -> dict | None:
    """Load ``[detection] institution_profile`` (allow-list facts), if named."""
    name = (config.get("detection") or {}).get("institution_profile") or None
    if not name:
        return None
    document = load_profile_json("institution", name, profiles_dir(config))
    if not isinstance(document, dict):
        raise ValueError("invalid institution profile")
    return document


def configure_finder_prompt(config: Mapping[str, Any], institution: Mapping | None) -> dict:
    """Apply ``[detection] finder_context`` and ``finder_prompt``, and the institution profile's
    ``finder_do_not_flag`` list (the deployment's own "do not list" items)."""
    detection = config.get("detection") or {}
    from .detectors.llm_finder import DEFAULT_FINDER_CONTEXT
    return configure_finder(
        context=detection.get("finder_context") or DEFAULT_FINDER_CONTEXT,
        prompt_version=detection.get("finder_prompt") or None,
        do_not_flag=(institution or {}).get("finder_do_not_flag"),
    )


def configure_versions(config: Mapping[str, Any]) -> dict:
    """Apply ``[detection] version`` / ``fixes`` and ``[policy] version`` / ``quasi_id_ask`` (defaults: v0.2).

    ``version = "v0.1"`` in both tables restores the evaluated v0.1 behaviour (kept as a negative control).
    """
    from .detectors.settings import configure_detection_fixes, configure_detection_version
    from .policy import configure_policy

    detection = config.get("detection") or {}
    policy = dict(config.get("policy") or {})
    if set(policy) - {"version", "quasi_id_ask"}:
        raise ValueError("unknown [policy] settings")
    return {
        "detection": configure_detection_version(detection.get("version") or None),
        "detection_fixes": configure_detection_fixes(detection.get("fixes")),
        "policy": configure_policy(policy.get("version") or None, quasi_id_ask=policy.get("quasi_id_ask")),
    }


HOOKS_KEYS = {
    "cloud_authorised", "roots", "allowed_tools", "drafts_dir",
    "destination_id", "model", "deployment_type",
}


def hooks_policy(config: Mapping[str, Any]):
    """Build the trusted Claude Code hook policy from ``[hooks]``.

    Absent or empty, the policy stays closed: preparation-only (prompts with
    personal data get a de-identified draft, everything else is refused), no
    workspace roots, so no file tool is ever admitted.
    """
    from .hooks.protocol import DEFAULT_TOOLS
    from .hooks.service import HookPolicy

    section = config.get("hooks") or {}
    if not isinstance(section, Mapping):
        raise ValueError("invalid [hooks] section")
    if set(section) - HOOKS_KEYS:
        raise ValueError("unknown [hooks] settings")
    values: dict[str, Any] = {}
    if "cloud_authorised" in section:
        if type(section["cloud_authorised"]) is not bool:
            raise ValueError("[hooks] cloud_authorised must be a boolean")
        values["cloud_authorised"] = section["cloud_authorised"]
    roots = section.get("roots", [])
    if not isinstance(roots, list) or any(type(item) is not str or not item.strip() for item in roots):
        raise ValueError("[hooks] roots must be a list of directory paths")
    resolved = []
    for item in roots:
        path = Path(item).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("[hooks] roots must be existing absolute directories")
        resolved.append(path)
    values["roots"] = tuple(resolved)
    if "allowed_tools" in section:
        tools = section["allowed_tools"]
        if not isinstance(tools, list) or any(type(item) is not str for item in tools):
            raise ValueError("[hooks] allowed_tools must be a list of tool names")
        if not set(tools) <= DEFAULT_TOOLS:
            raise ValueError("[hooks] allowed_tools names an unsupported tool")
        values["allowed_tools"] = frozenset(tools)
    drafts = section.get("drafts_dir") or None
    if drafts is not None:
        if type(drafts) is not str:
            raise ValueError("[hooks] drafts_dir must be a path")
        path = Path(drafts).expanduser()
        if not path.is_absolute():
            raise ValueError("[hooks] drafts_dir must be an absolute path")
        values["drafts_dir"] = path
    for key in ("destination_id", "model", "deployment_type"):
        if key in section:
            if type(section[key]) is not str:
                raise ValueError(f"[hooks] {key} must be text")
            values[key] = section[key]
    return HookPolicy(**values)


RUNTIME_PATH_KEYS = ("engines_dir", "marian_profiles_dir")
RUNTIME_TEXT_KEYS = ("marian_objective", "strata_api_key_env")


def runtime_settings(config: Mapping[str, Any]) -> RuntimeSettings:
    """Runtime settings file, an optional ``[runtime] catalogue`` profile and
    the back-end keys of ``[runtime]`` (non-empty values override the file)."""
    from dataclasses import replace

    section = config.get("runtime") or {}
    settings = RuntimeSettings.load(
        Path(section["settings_file"]).expanduser() if section.get("settings_file") else None
    )
    if section.get("catalogue"):
        settings = replace(
            settings,
            catalogue_file=profile_path("catalogue", section["catalogue"], profiles_dir(config)),
        )
    overrides: dict[str, Any] = {}
    for key in RUNTIME_PATH_KEYS + RUNTIME_TEXT_KEYS:
        value = section.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            raise ValueError("invalid runtime back-end setting")
        overrides[key] = Path(value).expanduser() if key in RUNTIME_PATH_KEYS else value
    return replace(settings, **overrides) if overrides else settings


def build_router(config_path):
    config = load_config(config_path)
    gate = config["gate"]
    if gate.get("synthetic_only") is not True:
        raise ValueError("synthetic-only deployment required")
    flag_names = (
        "synthetic_cloud_authorised", "local_authorised",
        "pilot_fixture_enabled", "operator_console_enabled",
    )
    if any(type(gate.get(name, False)) is not bool for name in flag_names):
        raise ValueError("pilot flags must be booleans")
    requester = gate["requester_identity"]
    operator = gate.get("operator_identity", "named-synthetic-operator")
    if operator == requester:
        raise PermissionError("configured operator cannot be the requester")
    register_config = config.get("register") or {}
    set_business_timezone(register_config.get("business_timezone") or None)
    configure_markings(config)
    configure_rule_packs(config)
    institution = institution_profile(config)
    configure_finder_prompt(config, institution)
    configure_versions(config)
    hooks = hooks_policy(config)
    reference = register_config.get("factory", DEFAULT_REGISTER_FACTORY)
    path = register_config.get("path") or None
    if reference == DEFAULT_REGISTER_FACTORY:
        register = register_factory(requester_identity=requester, path=path)
    else:
        module, name = reference.split(":", 1)
        register = getattr(importlib.import_module(module), name)()
    if not isinstance(register, SqliteRegister) or register.requester_identity != requester:
        raise ValueError("configured SQLite register/requester contract mismatch")
    settings = runtime_settings(config)
    catalogue = Catalogue.load(settings.catalogue_file)
    try:
        catalogue = catalogue.with_calibration(
            CalibrationStore(settings.storage_dir / "calibration.json"),
            read_identity(settings.gpu_index).fingerprint,
        )
    except Exception:
        pass
    runtime = build_runtime(settings, catalogue)
    vault = cloud = None
    try:
        vault_config = dict(config.get("vault") or {})
        vault = Vault(
            Path(vault_config["path"]).expanduser() if vault_config.get("path") else None,
            protector=build_protector(vault_config),
            insecure_test_only=vault_config.get("insecure_test_only", False) is True,
        )
        cloud = CloudClient(cloud_settings(config))
        policy = UseCasePolicy(
            "synthetic-pilot", (cloud.settings.destination_id,),
            approved_for_cloud=gate.get("synthetic_cloud_authorised", False),
            requires_in_region_inference=False,
        )
        fixture = (
            PilotAutoApprover(register, True, operator)
            if gate.get("pilot_fixture_enabled", False) else None
        )
        detection = config.get("detection") or {}
        router = Router(
            register=register, vault=vault, runtime=runtime, cloud=cloud,
            profile=DeploymentProfile(
                "synthetic-pilot", "1", (), (policy,), institution=institution,
            ),
            use_case=policy,
            targets=DetectionTargets(
                llm_client=SequentialFinderClient(runtime), thorough=True,
                tracer_model_directory=str(Path(
                    detection.get("tracer_model_directory")
                    or settings.models_dir / "pii-tracer"
                ).expanduser()),
                tracer_artefact_manifest_id=detection.get("tracer_artefact_manifest_id", ""),
            ),
            requester_identity=requester,
            allowed_tools=frozenset(gate.get("allowed_local_tools", [])),
            local_authorised=gate.get("local_authorised", False),
            pilot_fixture_enabled=fixture is not None,
            pilot_approve=(
                lambda job: fixture.try_approve(job.job_id)
                if fixture is not None else None
            ),
        )
        router.hooks_policy = hooks
        router.operator_console_enabled = gate.get("operator_console_enabled", False)
        router.operator_identity = operator
        router.runtime_settings = settings
        router.local_target = "laptop"
        router.capacity_file = (
            Path(config["capacity"]["file"]).expanduser()
            if config.get("capacity", {}).get("file") else None
        )
        router.model_available = lambda entry: (
            entry.runtime == "llamacpp"
            and settings.engine_path.is_file()
            and (settings.models_dir / entry.file).is_file()
        )
        # v0.3 catalogues ([known_miss], [false_positives], [tripwire]): all off unless enabled.
        from .catalogues.gate import configure as configure_catalogues
        configure_catalogues(router, config, protector_factory=lambda: build_protector(vault_config))
        # v0.3 detector canary ([canary], on by default): known-answer checks at start-up (run by `themis serve`),
        # after every model load by the scheduler, and periodically; a failure blocks cloud-bound work.
        from .canary import configure as configure_canary
        configure_canary(router, config)
        return router
    except BaseException:
        if cloud is not None:
            cloud.close()
        if vault is not None:
            vault.close()
        runtime.close()
        raise
