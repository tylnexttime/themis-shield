"""How the router applies the v0.3 catalogues. With every feature off, nothing here changes a decision.

- :func:`decide_with_catalogue` replaces the router's single ``decide`` call: without a false-positive catalogue it
  returns exactly ``decide(evidence)``; with one, the model's decision and the catalogue's override are kept apart.
- :func:`final_sweep` runs the known-miss register over the outbound de-identified JSON, after substitution. A hit
  or a stale snapshot can only move RELEASE to ASK; an unreadable register blocks.
- :func:`configure` reads ``[known_miss]``, ``[false_positives]`` and ``[tripwire]`` (all ``enabled = false`` by
  default) and attaches the enabled parts to the router.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..paths import app_data_dir
from ..policy import decide
from ..types import Decision, DecisionResult, ReasonCode
from .false_positives import FalsePositiveCatalogue, Override, match
from .known_miss import DEFAULT_MAX_SNAPSHOT_AGE_DAYS, KnownMissRegister, SweepHit, max_age
from .store import CatalogueUnavailable


KNOWN_MISS_KEYS = {"enabled", "path", "max_snapshot_age_days"}
FALSE_POSITIVE_KEYS = {"enabled", "path", "scope"}
TRIPWIRE_KEYS = {"enabled", "max_completion_tokens"}
DEFAULT_TRIPWIRE_TOKENS = 2048


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """Value-free: register version and age used, and where each hit is in the outbound text."""

    version: int
    as_of_utc: datetime
    stale: bool
    hits: tuple[SweepHit, ...] = ()

    @property
    def status(self) -> str:
        return "+".join(name for name, on in (("hit", bool(self.hits)), ("stale", self.stale)) if on) or "clear"

    def as_record(self) -> dict:
        return {
            "status": self.status, "version": self.version,
            "as_of_utc": self.as_of_utc.isoformat(timespec="seconds"),
            "hits": [hit.as_record() for hit in self.hits],
        }


@dataclass(frozen=True, slots=True)
class GateDecision(DecisionResult):
    """A decision with its parts kept apart: what detection plus policy said (``model``), what the false-positive
    catalogue overrode, and the known-miss sweep. ``decision``/``reasons`` are the final values."""

    model: DecisionResult | None = None
    overrides: tuple[Override, ...] = ()
    fpc_status: str = "off"
    sweep: SweepOutcome | None = None

    def record(self) -> dict:
        model = self.model or DecisionResult(self.decision, self.reasons)
        return {
            "model_decision": model.decision.value,
            "model_reasons": [item.value for item in model.reasons],
            "final_decision": self.decision.value,
            "final_reasons": [item.value for item in self.reasons],
            "false_positive_catalogue": {
                "status": self.fpc_status,
                "overrides": [item.as_record() for item in self.overrides],
            },
            "known_miss": self.sweep.as_record() if self.sweep is not None else {"status": "off"},
        }


def _gate(result: DecisionResult, **changes) -> GateDecision:
    if isinstance(result, GateDecision):
        return replace(result, **changes)
    values = {"decision": result.decision, "reasons": result.reasons, "model": result}
    values.update(changes)
    return GateDecision(**values)


# -- false-positive catalogue -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class FalsePositiveGate:
    catalogue: FalsePositiveCatalogue = field(repr=False)
    scope: str


def decide_with_catalogue(router, text: str, evidence):
    """``(decision, evidence for substitution)``. Without a catalogue: ``(decide(evidence), evidence)``, unchanged.

    With one, the decision is recomputed from the same evidence with the matched findings set aside for holds;
    ``allow`` matches are also removed from substitution. Markings, the injection signal, stage failures and
    secrets are untouched, so an entry can lift a hold it is the sole cause of and nothing else. An unreadable
    catalogue is ignored (the stricter decision stands) and recorded as ``unavailable``.
    """
    model = decide(evidence, router.use_case)
    gate = getattr(router, "fp_catalogue", None)
    if gate is None:
        return model, evidence
    try:
        entries = gate.catalogue.active(gate.scope, router.clock())
    except Exception:
        return _gate(model, fpc_status="unavailable"), evidence
    if not entries:
        return _gate(model, fpc_status="no-entries"), evidence
    no_hold, allowed, overrides = match(entries, text, evidence.findings)
    if not no_hold:
        return _gate(model, fpc_status="no-match"), evidence
    hold_view = replace(evidence, findings=tuple(
        item for index, item in enumerate(evidence.findings) if index not in no_hold
    ))
    substitution = replace(evidence, findings=tuple(
        item for index, item in enumerate(evidence.findings) if index not in allowed
    ))
    final = decide(hold_view, router.use_case)
    if model.decision == Decision.BLOCK:
        final = model
    return GateDecision(
        final.decision, final.reasons, model=model, overrides=overrides, fpc_status="applied",
    ), substitution


# -- known-miss register ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class KnownMissSweep:
    register: KnownMissRegister = field(repr=False)
    max_age: timedelta = timedelta(days=DEFAULT_MAX_SNAPSHOT_AGE_DAYS)


_SWEPT_ROOTS = ("messages", "tools", "stop")


def outbound_leaves(body) -> Iterator[tuple[tuple, str]]:
    """Every text leaf the cloud will read, with its path. Tool-call arguments are decoded too."""
    from ..cloud.openai_compat import strict_json

    def walk(value, path):
        if isinstance(value, str):
            yield path, value
            if path and path[-1] == "arguments":
                try:
                    decoded = strict_json(value)
                except ValueError:
                    return
                yield from walk(decoded, (*path, "decoded"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from walk(item, (*path, index))
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from walk(item, (*path, key))

    for root in _SWEPT_ROOTS:
        if isinstance(body, dict) and root in body:
            yield from walk(body[root], (root,))


def final_sweep(router, raw: bytes, result: DecisionResult) -> DecisionResult:
    """Sweep the exact outbound JSON. Without a register: ``result`` unchanged.

    A hit or a stale snapshot adds ASK reasons (RELEASE becomes ASK); nothing is redacted. An unreadable register
    raises ``GateFailure(BLOCK:known-miss-unreadable)``.
    """
    sweeper = getattr(router, "known_miss", None)
    if sweeper is None:
        return result
    from ..cloud.openai_compat import strict_json
    from ..router import GateFailure
    try:
        snapshot = sweeper.register.snapshot()
        hits = snapshot.sweep(outbound_leaves(strict_json(raw)))
    except Exception:
        raise GateFailure(ReasonCode.KNOWN_MISS_UNREADABLE.value, 200) from None
    stale = snapshot.is_stale(router.clock(), sweeper.max_age)
    outcome = SweepOutcome(snapshot.version, snapshot.as_of_utc, stale, hits)
    reasons = list(result.reasons)
    if hits:
        reasons.append(ReasonCode.KNOWN_MISS_HIT)
    if stale:
        reasons.append(ReasonCode.KNOWN_MISS_STALE)
    decision = result.decision
    if (hits or stale) and decision == Decision.RELEASE:
        decision = Decision.ASK
    return _gate(result, decision=decision, reasons=tuple(dict.fromkeys(reasons)), sweep=outcome)


def sweep_findings_text(result) -> str:
    """The plain-text finding list shown with a held job (value-free: entries and offsets only)."""
    sweep = getattr(result, "sweep", None)
    if sweep is None or (not sweep.hits and not sweep.stale):
        return ""
    lines = []
    if sweep.hits:
        lines.append("Known-miss register hits in the de-identified text (check them before confirming):")
        lines.extend("- " + hit.describe() for hit in sweep.hits)
    if sweep.stale:
        lines.append(
            f"The known-miss register snapshot (version {sweep.version}) is older than the configured limit; "
            "an Operator refreshes it with `themis kmr refresh`."
        )
    return "\n" + "\n".join(lines)


# -- configuration ----------------------------------------------------------------------------------------------


def _section(config: Mapping[str, Any], name: str, keys: set[str]) -> dict:
    section = config.get(name) or {}
    if not isinstance(section, Mapping) or set(section) - keys:
        raise ValueError(f"unknown [{name}] settings")
    enabled = section.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError(f"[{name}] enabled must be a boolean")
    return dict(section)


def _path(section: Mapping[str, Any], default: str) -> Path:
    value = section.get("path") or ""
    if not isinstance(value, str):
        raise ValueError("catalogue path must be text")
    return Path(value).expanduser() if value else app_data_dir() / default


def settings(config: Mapping[str, Any]) -> dict:
    """Validated catalogue settings. Every feature is off unless its section says ``enabled = true``."""
    known = _section(config, "known_miss", KNOWN_MISS_KEYS)
    false = _section(config, "false_positives", FALSE_POSITIVE_KEYS)
    trip = _section(config, "tripwire", TRIPWIRE_KEYS)
    age = known.get("max_snapshot_age_days", DEFAULT_MAX_SNAPSHOT_AGE_DAYS)
    max_age(age)
    tokens = trip.get("max_completion_tokens", DEFAULT_TRIPWIRE_TOKENS)
    if type(tokens) is not int or not 64 <= tokens <= 32768:
        raise ValueError("[tripwire] max_completion_tokens must be between 64 and 32768")
    scope = false.get("scope") or ""
    if not isinstance(scope, str):
        raise ValueError("[false_positives] scope must be text")
    if not scope:
        scope = ((config.get("detection") or {}).get("institution_profile") or "default")
    return {
        "known_miss": {
            "enabled": known.get("enabled", False), "path": _path(known, "known-miss.db"),
            "max_age": max_age(age),
        },
        "false_positives": {
            "enabled": false.get("enabled", False), "path": _path(false, "false-positives.db"), "scope": scope,
        },
        "tripwire": {"enabled": trip.get("enabled", False), "max_completion_tokens": tokens},
    }


def configure(router, config: Mapping[str, Any], *, protector_factory=None) -> dict:
    """Attach the enabled catalogue features to ``router``; disabled ones stay ``None`` (v0.2.1 behaviour)."""
    from .tripwire import Tripwire

    values = settings(config)
    router.known_miss = router.fp_catalogue = router.tripwire = None
    known = values["known_miss"]
    if known["enabled"]:
        protector = protector_factory() if protector_factory is not None else None
        router.known_miss = KnownMissSweep(
            KnownMissRegister(
                known["path"], protector=protector, clock=router.clock, create=True,
                insecure_test_only=(config.get("vault") or {}).get("insecure_test_only") is True,
            ),
            known["max_age"],
        )
    false = values["false_positives"]
    if false["enabled"]:
        router.fp_catalogue = FalsePositiveGate(
            FalsePositiveCatalogue(false["path"], clock=router.clock, create=True), false["scope"],
        )
    trip = values["tripwire"]
    if trip["enabled"]:
        router.tripwire = Tripwire(trip["max_completion_tokens"])
    return values


def any_enabled(router) -> bool:
    return any(getattr(router, name, None) is not None for name in ("known_miss", "fp_catalogue", "tripwire"))


__all__ = [
    "CatalogueUnavailable", "FalsePositiveGate", "GateDecision", "KnownMissSweep", "SweepOutcome",
    "any_enabled", "configure", "decide_with_catalogue", "final_sweep", "outbound_leaves", "settings",
    "sweep_findings_text",
]
