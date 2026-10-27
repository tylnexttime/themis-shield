"""Detector canary (v0.3): known-answer checks against silent detector failure.

Every detection stage fails closed when it crashes. A stage that "succeeds" with nothing does not: wrong or damaged
weights, a tokenizer mismatch, or a chat-template change in the inference engine (for example a template that turns
a model's thinking mode on, so a constrained JSON answer comes back empty) all produce a valid, empty answer. The
canary runs a small fixed set of synthetic documents through each detector and checks conservative minimum
expectations: at least one finding of the expected kind overlapping the expected place, never an exact span set, so
ordinary model variation does not make it flaky.

Detectors and expectations (``EXPECTATIONS`` below):

- ``rules``: the e-mail address; with the ``au`` rule pack also the phone number, the TFN and the Medicare number;
  and nothing at all in the clean control sentence (the rule packs are deterministic).
- ``pii-tracer``: the person's name and the e-mail address.
- ``propagate``: given the name found once, the second mention of the surname.
- each local LLM finder: the person's name and the indirect health statement (any sensitive-statement label).

When it runs (``[canary]``): at gate start-up, before serving (a failure refuses to start); after every model load
by the runtime scheduler (the finder canary for that model, inside the same residency, before any document); and
periodically (``interval_minutes``, default 30, and optionally every ``every_documents`` cloud-bound documents). A
failed periodic or reload canary puts the gate in a degraded state: cloud-bound work is blocked
(``DETECTOR_CANARY_FAILED``) and local-only work still runs, until a canary covering the failed detector passes
again (a failed detector is re-checked at the next opportunity, not only at the next interval).

Records are value-free: detector, pass or fail, the fixed names of failed expectations, a fixed error code and the
duration. The only text the canary ever handles is its own fixed synthetic documents. All values are invented:
a phone number from the range reserved for fiction, an address under a domain reserved for documentation
(RFC 2606), and a TFN and a Medicare number whose check digits are computed below from sequential test-style bodies.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import threading
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping

from .detectors.contracts import DetectionTargets, Status, finding
from .detectors.llm_finder import FinderModel, LLMFinder
from .detectors.propagate import propagate
from .detectors.rules import (
    MEDICARE_WEIGHTS, TFN_WEIGHTS, RulesDetector, active_rule_packs, validate_identifier, validate_rule_packs,
)
from .types import Label

CANARY_VERSION = "canary-v1"
# The canary documents are short; a bounded answer also bounds the time a model that wanders off (into a thinking
# channel, for example) can take. Truncation fails the check, which is the point.
CANARY_MAX_TOKENS = 1024
FINDER_MODELS = (FinderModel.E4B, FinderModel.GEMMA_26B_A4B)
TIER1_DETECTORS = ("rules", "pii-tracer", "propagate")
SENSITIVE_STATEMENT_LABELS = frozenset({
    Label.HEALTH, Label.DISABILITY, Label.INDIGENOUS, Label.SEXUALITY, Label.RELIGION, Label.CRIMINAL,
    Label.FAMILY_VIOLENCE, Label.ETHNICITY, Label.UNION,
})
BLOCK_FAILED = "DETECTOR_CANARY_FAILED"
BLOCK_PENDING = "DETECTOR_CANARY_PENDING"
TRIGGERS = ("startup", "reload", "periodic", "documents", "retry", "pending", "manual", "eval")


def _check_digit_tfn(body: str) -> str:
    total = sum(int(digit) * weight for digit, weight in zip(body, TFN_WEIGHTS))
    for last in range(10):
        if (total + last * TFN_WEIGHTS[8]) % 11 == 0:
            return body + str(last)
    raise ValueError("no TFN check digit for this body")


def _check_digit_medicare(body: str, issue: str = "1") -> str:
    return body + str(sum(int(digit) * weight for digit, weight in zip(body, MEDICARE_WEIGHTS)) % 10) + issue


def _group(digits: str, sizes: Iterable[int]) -> str:
    parts, position = [], 0
    for size in sizes:
        parts.append(digits[position:position + size])
        position += size
    return " ".join(parts)


# ----------------------------------------------------------------------------------------------------------------------
# The fixed synthetic documents. Every value is invented.
# ----------------------------------------------------------------------------------------------------------------------
NAME = "Oriel Brackenbury"
SURNAME = "Brackenbury"
EMAIL = "oriel.brackenbury@example.org"
PHONE = "0491 570 156"  # inside the mobile range set aside for fiction
TFN = _group(_check_digit_tfn("12345678"), (3, 3, 3))
MEDICARE = _group(_check_digit_medicare("21234567"), (4, 5, 1))
STATEMENT = "is on dialysis three mornings a week"

DOCUMENTS = {
    "identifiers": (
        f"Synthetic canary record. {NAME} can be reached on {PHONE} or at {EMAIL}. "
        f"Her TFN is {TFN} and her Medicare number is {MEDICARE}."
    ),
    "statement": (
        f"Synthetic canary note. {NAME} {STATEMENT}, so Ms {SURNAME} will join the planning meeting by video."
    ),
    "control": "The quarterly newsletter goes out on the first Monday of each month.",
}
assert validate_identifier(Label.TFN, TFN).checksum_valid
assert validate_identifier(Label.MEDICARE, MEDICARE).checksum_valid


def _spans(document: str, value: str, occurrence: int | None = None) -> tuple[tuple[int, int], ...]:
    text, found, start = DOCUMENTS[document], [], 0
    while (index := text.find(value, start)) >= 0:
        found.append((index, index + len(value)))
        start = index + 1
    if not found:
        raise ValueError("canary value missing from its document")
    return tuple(found) if occurrence is None else (found[occurrence],)


@dataclass(frozen=True, slots=True)
class Expectation:
    """At least one finding with one of ``labels`` overlapping one of ``spans`` (``labels`` empty: no findings)."""

    name: str
    document: str
    labels: frozenset[Label] = frozenset()
    spans: tuple[tuple[int, int], ...] = ()

    def met(self, findings) -> bool:
        if not self.labels:
            return not findings
        return any(
            item.labels & self.labels and any(item.start < end and start < item.end for start, end in self.spans)
            for item in findings
        )


def _expect(name, document, labels, value=None, occurrence=None) -> Expectation:
    labels = frozenset({labels} if isinstance(labels, Label) else labels)
    return Expectation(name, document, labels, _spans(document, value, occurrence) if value else ())


RULES_CORE = (
    _expect("EMAIL", "identifiers", Label.EMAIL, EMAIL),
    _expect("CONTROL_CLEAN", "control", frozenset()),
)
RULES_AU = (
    _expect("PHONE", "identifiers", Label.PHONE, PHONE),
    _expect("TFN", "identifiers", Label.TFN, TFN),
    _expect("MEDICARE", "identifiers", Label.MEDICARE, MEDICARE),
)
TRACER = (
    _expect("PERSON", "identifiers", Label.PERSON, NAME),
    _expect("EMAIL", "identifiers", Label.EMAIL, EMAIL),
)
# The seed is the name's first mention; only propagation can reach the second mention of the surname.
PROPAGATION_SEED = _spans("statement", NAME, 0)[0]
PROPAGATION = (_expect("REPEATED_NAME", "statement", Label.PERSON, SURNAME, 1),)
FINDER = (
    Expectation("PERSON", "statement", frozenset({Label.PERSON}), _spans("statement", NAME) + _spans("statement", SURNAME)),
    _expect("SENSITIVE_STATEMENT", "statement", SENSITIVE_STATEMENT_LABELS, STATEMENT),
)


def rules_expectations(packs=None) -> tuple[Expectation, ...]:
    packs = validate_rule_packs(packs) if packs is not None else active_rule_packs()
    return RULES_CORE + (RULES_AU if "au" in packs else ())


def expectations(packs=None) -> dict[str, tuple[str, ...]]:
    """The fixed expectation names per detector (for documentation and records)."""
    names = {
        "rules": tuple(item.name for item in rules_expectations(packs)),
        "pii-tracer": tuple(item.name for item in TRACER),
        "propagate": tuple(item.name for item in PROPAGATION),
    }
    for model in FINDER_MODELS:
        names[model.value] = tuple(item.name for item in FINDER)
    return names


# ----------------------------------------------------------------------------------------------------------------------
# Results (value-free)
# ----------------------------------------------------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class DetectorCheck:
    detector: str
    status: str  # "pass", "fail" or "skipped"
    failed: tuple[str, ...] = ()
    duration_ms: float = 0.0
    error_code: str | None = None  # fixed codes only
    trigger: str = "manual"
    checked_utc: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def record(self) -> dict:
        return {
            "detector": self.detector, "status": self.status, "failed": list(self.failed),
            "duration_ms": round(self.duration_ms, 1), "error_code": self.error_code,
            "trigger": self.trigger, "checked_utc": self.checked_utc,
        }


@dataclass(frozen=True, slots=True)
class CanaryResult:
    trigger: str
    checked_utc: str
    checks: tuple[DetectorCheck, ...]

    @property
    def passed(self) -> bool:
        """Every check passed or was deliberately skipped (a skip is recorded, never counted as a pass)."""
        return all(item.status in {"pass", "skipped"} for item in self.checks)

    @property
    def failures(self) -> tuple[DetectorCheck, ...]:
        return tuple(item for item in self.checks if item.status == "fail")

    def record(self) -> dict:
        return {
            "version": CANARY_VERSION, "trigger": self.trigger, "checked_utc": self.checked_utc,
            "passed": self.passed, "checks": [item.record() for item in self.checks],
        }


def _check(detector: str, expected, run: Callable[[], Mapping[str, Any]], trigger: str,
           clock: Callable[[], datetime] = utc_now) -> DetectorCheck:
    """Run one detector over the documents it needs and evaluate its expectations; never raises."""
    from .detectors.engine import run_stage

    began = perf_counter()
    failed, error = [], None
    try:
        results = {}
        for document, call in run().items():
            result = run_stage(DOCUMENTS[document], detector, call)
            if result.report.status != Status.OK:
                error = result.report.error_code or "stage-failed"
                break
            results[document] = result.findings
        if error is None:
            failed = [item.name for item in expected if not item.met(results[item.document])]
        else:
            failed = ["STAGE_OK"]
    except BaseException as caught:  # noqa: BLE001 - the canary itself must fail closed, never crash its caller
        if isinstance(caught, (KeyboardInterrupt, SystemExit)):
            raise
        failed, error = ["STAGE_OK"], "canary-error"
    return DetectorCheck(
        detector, "fail" if failed else "pass", tuple(failed), (perf_counter() - began) * 1000, error, trigger,
        _stamp(clock()),
    )


def check_rules(trigger: str = "manual", packs=None, clock=utc_now) -> DetectorCheck:
    detector = RulesDetector(packs=tuple(packs) if packs is not None else None)
    return _check("rules", rules_expectations(packs), lambda: {
        "identifiers": lambda: detector.detect(DOCUMENTS["identifiers"]),
        "control": lambda: detector.detect(DOCUMENTS["control"]),
    }, trigger, clock)


def check_tracer(tracer, trigger: str = "manual", clock=utc_now) -> DetectorCheck:
    return _check("pii-tracer", TRACER, lambda: {
        "identifiers": lambda: tracer.detect(DOCUMENTS["identifiers"]),
    }, trigger, clock)


def check_propagation(trigger: str = "manual", clock=utc_now) -> DetectorCheck:
    from .detectors.engine import _simple

    seed = (finding(*PROPAGATION_SEED, Label.PERSON, "canary-seed", 1),)
    text = DOCUMENTS["statement"]
    return _check("propagate", PROPAGATION, lambda: {
        "statement": lambda: _simple("propagate", propagate(text, seed)),
    }, trigger, clock)


def check_finder(model: FinderModel, client, trigger: str = "manual", max_tokens: int = CANARY_MAX_TOKENS,
                 clock=utc_now) -> DetectorCheck:
    """``client`` is a resident inference target or any ChatJSONClient (never a residency context)."""
    model = FinderModel(model)
    return _check(model.value, FINDER, lambda: {
        "statement": lambda: LLMFinder(model, client, max_tokens).detect(DOCUMENTS["statement"]),
    }, trigger, clock)


def skipped(detector: str, reason: str, trigger: str, clock=utc_now) -> DetectorCheck:
    return DetectorCheck(detector, "skipped", (), 0.0, reason, trigger, _stamp(clock()))


def check_finder_via(model: FinderModel, finder_client, trigger: str, max_tokens: int = CANARY_MAX_TOKENS,
                     clock=utc_now) -> DetectorCheck:
    """Run a finder check through a client that may offer a residency (the gate's SequentialFinderClient)."""
    residency = getattr(finder_client, "residency", None)
    began = perf_counter()
    try:
        if residency is None:
            return check_finder(model, finder_client, trigger, max_tokens, clock)
        with residency(FinderModel(model).value) as resident:
            return check_finder(model, resident, trigger, max_tokens, clock)
    except BaseException as caught:  # noqa: BLE001 - a model that will not load fails the check
        if isinstance(caught, (KeyboardInterrupt, SystemExit)):
            raise
        return DetectorCheck(
            FinderModel(model).value, "fail", ("STAGE_OK",), (perf_counter() - began) * 1000, "residency-failed",
            trigger, _stamp(clock()),
        )


def tier1_checks(targets: DetectionTargets | None, trigger: str, packs=None, clock=utc_now) -> list[DetectorCheck]:
    from .detectors.engine import tracer_for

    checks = [check_rules(trigger, packs, clock)]
    try:
        tracer = tracer_for(targets if targets is not None else DetectionTargets())
    except Exception:  # noqa: BLE001 - an unbuildable tracer fails its check
        checks.append(DetectorCheck("pii-tracer", "fail", ("STAGE_OK",), 0.0, "canary-error", trigger,
                                    _stamp(clock())))
    else:
        checks.append(check_tracer(tracer, trigger, clock))
    checks.append(check_propagation(trigger, clock))
    return checks


def run_canary(targets: DetectionTargets | None = None, *, finder_client=None, trigger: str = "manual",
               finders: Iterable[FinderModel] = FINDER_MODELS, finders_skipped: str | None = None, packs=None,
               max_tokens: int = CANARY_MAX_TOKENS, clock=utc_now) -> CanaryResult:
    """Run every detector's canary once. ``finders_skipped`` (a fixed reason) records the finders as skipped."""
    if trigger not in TRIGGERS:
        raise ValueError("unknown canary trigger")
    checks = tier1_checks(targets, trigger, packs, clock)
    for model in finders:
        if finders_skipped is not None:
            checks.append(skipped(FinderModel(model).value, finders_skipped, trigger, clock))
        elif finder_client is None:
            checks.append(DetectorCheck(FinderModel(model).value, "fail", ("STAGE_OK",), 0.0, "no-finder-client",
                                        trigger, _stamp(clock())))
        else:
            checks.append(check_finder_via(model, finder_client, trigger, max_tokens, clock))
    return CanaryResult(trigger, _stamp(clock()), tuple(checks))


# ----------------------------------------------------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------------------------------------------------
CANARY_KEYS = {"enabled", "at_startup", "after_reload", "interval_minutes", "every_documents"}


@dataclass(frozen=True, slots=True)
class CanarySettings:
    enabled: bool = True
    at_startup: bool = True
    after_reload: bool = True
    interval_minutes: float = 30.0  # 0 = no periodic canary
    every_documents: int = 0  # 0 = off

    def __post_init__(self):
        for name in ("enabled", "at_startup", "after_reload"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"[canary] {name} must be a boolean")
        interval = self.interval_minutes
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0 <= interval <= 10080:
            raise ValueError("[canary] interval_minutes must be a number from 0 to 10080")
        if type(self.every_documents) is not int or not 0 <= self.every_documents <= 1_000_000:
            raise ValueError("[canary] every_documents must be a whole number from 0")

    def record(self) -> dict:
        return {name: getattr(self, name) for name in sorted(CANARY_KEYS)}


def canary_settings(config: Mapping[str, Any]) -> CanarySettings:
    section = config.get("canary") or {}
    if not isinstance(section, Mapping):
        raise ValueError("invalid [canary] section")
    if set(section) - CANARY_KEYS:
        raise ValueError("unknown [canary] settings")
    return CanarySettings(**section)


# ----------------------------------------------------------------------------------------------------------------------
# The gate's monitor: state, scheduling, gating and records
# ----------------------------------------------------------------------------------------------------------------------
class CanaryMonitor:
    """Holds the latest check per detector and decides whether cloud-bound work may proceed.

    Never hold ``_state`` while acquiring the model scheduler: the scheduler calls :meth:`after_acquire` from inside
    its own slot, possibly on another thread.
    """

    def __init__(self, settings: CanarySettings, *, targets: DetectionTargets | None = None, runtime=None,
                 finder_client=None, recorder: Callable[[dict], Any] | None = None, clock=utc_now,
                 finders: Iterable[FinderModel] = FINDER_MODELS, max_tokens: int = CANARY_MAX_TOKENS):
        self.settings = settings
        self.targets = targets
        self.runtime = runtime
        if finder_client is None and runtime is not None:
            from .runtime.modes import SequentialFinderClient
            finder_client = SequentialFinderClient(runtime)
        self.finder_client = finder_client
        self.recorder = recorder
        self.clock = clock
        self.finders = tuple(FinderModel(model) for model in finders)
        self.max_tokens = max_tokens
        self.required = TIER1_DETECTORS + tuple(model.value for model in self.finders)
        self._state = threading.RLock()
        self._tier1_lock = threading.Lock()
        self._local = threading.local()
        self._latest: dict[str, DetectorCheck] = {}
        self._checked_at: dict[str, datetime] = {}
        self._documents_due: set[str] = set()
        self._documents = 0
        self.record_failures = 0
        self.history: deque[dict] = deque(maxlen=50)

    # -- state ------------------------------------------------------------------------------------------------

    def _apply(self, checks: Iterable[DetectorCheck], trigger: str) -> CanaryResult:
        checks = tuple(checks)
        now = self.clock()
        with self._state:
            for item in checks:
                if item.status == "skipped":
                    continue
                self._latest[item.detector] = item
                self._checked_at[item.detector] = now
                self._documents_due.discard(item.detector)
        result = CanaryResult(trigger, _stamp(now), checks)
        record = result.record()
        with self._state:
            self.history.append(record)
        if self.recorder is not None:
            try:
                self.recorder(record)
            except Exception:  # noqa: BLE001 - the register reports its own unavailability on the job path
                with self._state:
                    self.record_failures += 1
        return result

    def _due(self, detector: str, now: datetime) -> str | None:
        """Why ``detector`` should be checked now, or None."""
        with self._state:
            latest = self._latest.get(detector)
            if latest is None:
                return "pending"
            if not latest.passed:
                return "retry"
            if detector in self._documents_due:
                return "documents"
            interval = self.settings.interval_minutes
            if interval and now - self._checked_at[detector] >= timedelta(minutes=interval):
                return "periodic"
        return None

    def block_reason(self) -> str | None:
        """``DETECTOR_CANARY_FAILED`` while any required detector's latest canary failed; ``..._PENDING`` while one
        has never been checked; otherwise None."""
        with self._state:
            if any(not self._latest[name].passed for name in self.required if name in self._latest):
                return BLOCK_FAILED
            if any(name not in self._latest for name in self.required):
                return BLOCK_PENDING
        return None

    def status(self) -> dict:
        """Value-free status for the web app and /health."""
        with self._state:
            reason = self.block_reason()
            detectors = {}
            for name in self.required:
                latest = self._latest.get(name)
                detectors[name] = latest.record() if latest is not None else {
                    "detector": name, "status": "not-yet-run", "failed": [], "duration_ms": None,
                    "error_code": None, "trigger": None, "checked_utc": None,
                }
            return {
                "enabled": True, "version": CANARY_VERSION,
                "state": "healthy" if reason is None else ("degraded" if reason == BLOCK_FAILED else "pending"),
                "reason": reason, "cloud_blocked": reason is not None, "detectors": detectors,
                "settings": self.settings.record(), "record_failures": self.record_failures,
            }

    # -- runs -------------------------------------------------------------------------------------------------

    def run(self, trigger: str = "manual") -> CanaryResult:
        """Check every detector now (start-up, manual). The finder checks use their own residencies."""
        if trigger not in TRIGGERS:
            raise ValueError("unknown canary trigger")
        with self._tier1_lock:
            checks = tier1_checks(self.targets, trigger, clock=self.clock)
        self._local.suppress = True
        try:
            for model in self.finders:
                if self.finder_client is None:
                    checks.append(DetectorCheck(model.value, "fail", ("STAGE_OK",), 0.0, "no-finder-client",
                                                trigger, _stamp(self.clock())))
                else:
                    checks.append(check_finder_via(model, self.finder_client, trigger, self.max_tokens, self.clock))
        finally:
            self._local.suppress = False
        return self._apply(checks, trigger)

    def startup(self) -> CanaryResult:
        return self.run("startup")

    def before_cloud_work(self, documents: int = 1) -> CanaryResult | None:
        """Called before cloud-bound detection: counts documents and runs the tier-1 canary when it is due.

        The finder canaries run inside their model's residency (:meth:`after_acquire`), so no model is loaded here.
        """
        now = self.clock()
        every = self.settings.every_documents
        with self._state:
            self._documents += max(0, int(documents))
            if every and self._documents >= every:
                self._documents = 0
                self._documents_due.update(self.required)
        reasons = {name: self._due(name, now) for name in TIER1_DETECTORS}
        if not any(reasons.values()):
            return None
        trigger = next(reason for reason in ("retry", "pending", "documents", "periodic") if reason in reasons.values())
        with self._tier1_lock:
            checks = tier1_checks(self.targets, trigger, clock=self.clock)
        return self._apply(checks, trigger)

    def after_acquire(self, entry, target, loaded: bool) -> CanaryResult | None:
        """Scheduler hook, called inside the model's slot before any document: the reload (or due) finder canary.

        Never raises: a canary that cannot run fails, and the failure blocks cloud-bound work.
        """
        if getattr(self._local, "suppress", False):
            return None
        models = self._models_for(entry)
        if not models:
            return None
        now = self.clock()
        checks, trigger = [], None
        for model in models:
            reason = "reload" if loaded and self.settings.after_reload else self._due(model.value, now)
            if reason is None:
                continue
            trigger = trigger or reason
            checks.append(check_finder(model, target, reason, self.max_tokens, self.clock))
        return self._apply(checks, trigger) if checks else None

    def _models_for(self, entry) -> tuple[FinderModel, ...]:
        identifier = getattr(entry, "id", None)
        resolve = getattr(self.runtime, "_resolve", None)
        found = []
        for model in self.finders:
            try:
                resolved = resolve(model.value).id if resolve is not None else model.value
            except Exception:  # noqa: BLE001 - a role the catalogue does not know is not this entry
                continue
            if resolved == identifier:
                found.append(model)
        return tuple(found)


def configure(router, config: Mapping[str, Any]) -> CanarySettings:
    """Attach a monitor to the router (``[canary] enabled``, default on) and hook it into the model scheduler."""
    settings = canary_settings(config)
    router.canary = None
    if not settings.enabled:
        return settings
    register = getattr(router, "register", None)
    recorder = getattr(register, "record_canary", None)
    monitor = CanaryMonitor(
        settings, targets=router.targets, runtime=router.runtime,
        finder_client=getattr(router.targets, "llm_client", None),
        recorder=(lambda record: recorder(record, now=router.clock())) if recorder is not None else None,
        clock=router.clock,
    )
    if hasattr(router.runtime, "load_listener"):
        router.runtime.load_listener = monitor.after_acquire
    router.canary = monitor
    return settings


__all__ = [
    "BLOCK_FAILED", "BLOCK_PENDING", "CANARY_MAX_TOKENS", "CANARY_VERSION", "CanaryMonitor", "CanaryResult",
    "CanarySettings", "DOCUMENTS", "DetectorCheck", "Expectation", "FINDER_MODELS", "canary_settings",
    "check_finder", "check_finder_via", "check_propagation", "check_rules", "check_tracer", "configure",
    "expectations", "run_canary", "rules_expectations", "tier1_checks",
]
