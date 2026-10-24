"""Define immutable finding, policy, stage and request-evidence data types.

Offsets are half-open Python character positions, not bytes or UTF-16 units.
Dataclasses describe contracts; their invariants are not implemented here.
Sensitive strings are excluded from generated representations where practical.
That is not a substitute for value-free logging and crash-dump controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


type ContentPath = tuple[str | int, ...]
type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)


class Label(StrEnum):
    """Native detector labels; security markings are a separate BLOCK signal.

    The first 24 are the original labels (prompt v1, PII-Tracer aliases, rule packs). The last six are generic
    additions used by finder prompt v2: INTERNAL_ID and NATIONAL_ID are the sector- and country-neutral identifier
    labels, and ETHNICITY and UNION are sensitive like the other statement labels.
    """

    PERSON = "PERSON"
    STUDENT_ID = "STUDENT_ID"
    USI = "USI"
    TFN = "TFN"
    MEDICARE = "MEDICARE"
    ABN = "ABN"
    DRIVER_LICENCE = "DRIVER_LICENCE"
    PASSPORT = "PASSPORT"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ADDRESS = "ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    OTHER_DATE = "OTHER_DATE"
    URL_PERSONAL = "URL_PERSONAL"
    SECRET = "SECRET"
    QUASI_ID = "QUASI_ID"
    HEALTH = "HEALTH"
    DISABILITY = "DISABILITY"
    INDIGENOUS = "INDIGENOUS"
    SEXUALITY = "SEXUALITY"
    RELIGION = "RELIGION"
    CRIMINAL = "CRIMINAL"
    FAMILY_VIOLENCE = "FAMILY_VIOLENCE"
    INTERNAL_ID = "INTERNAL_ID"
    NATIONAL_ID = "NATIONAL_ID"
    PAYMENT_CARD = "PAYMENT_CARD"
    USERNAME = "USERNAME"
    ETHNICITY = "ETHNICITY"
    UNION = "UNION"


class Decision(StrEnum):
    RELEASE = "RELEASE"
    ASK = "ASK"
    BLOCK = "BLOCK"


class ReasonCode(StrEnum):
    STAGE_FAILED = "BLOCK:stage-failed"
    STAGE_TIMEOUT = "BLOCK:stage-timeout"
    STAGE_MISSING = "BLOCK:stage-missing"
    MALFORMED_OUTPUT = "BLOCK:malformed-output"
    INCOMPLETE_OUTPUT = "BLOCK:incomplete-output"
    UNMATCHED_LLM_VALUES = "ASK:unmatched-llm-values"
    SECRET = "BLOCK:secret"
    SECURITY_MARKING = "BLOCK:security-marking"
    NON_CONVERGENCE = "BLOCK:non-convergence"
    UNSUPPORTED_CONTENT = "BLOCK:unsupported-content"
    DESTINATION_DENIED = "BLOCK:destination-denied"
    AUTHENTICATION_FAILED = "BLOCK:authentication-failed"
    VAULT_UNAVAILABLE = "BLOCK:vault-unavailable"
    TOKEN_EXHAUSTED = "BLOCK:token-exhausted"
    PAYLOAD_CHANGED = "BLOCK:payload-changed"
    SENSITIVE = "ASK:sensitive"
    DISAGREEMENT_EMPTY_LLM = "ASK:disagreement-empty-llm"
    DISAGREEMENT_FEW_PERSONS = "ASK:disagreement-few-persons"
    QUASI_ID = "ASK:quasi-identifier"
    INJECTION_SIGNAL = "ASK:injection-signal"
    AMBIGUOUS_ENTITY = "ASK:ambiguous-entity"
    UNKNOWN_TOKEN = "RESTORE:unknown-token"
    DROPPED_TOKEN = "RESTORE:dropped-token"
    INVENTED_TOKEN = "RESTORE:invented-token"
    MANGLED_TOKEN = "RESTORE:mangled-token"
    AMBIGUOUS_TOKEN = "RESTORE:ambiguous-token"


class StageState(StrEnum):
    SUCCEEDED = "succeeded"
    ERROR = "error"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    SKIPPED = "skipped"


class LedgerKind(StrEnum):
    FINDING = "finding"
    STAGE = "stage"
    ACTION = "action"
    EXEMPTION = "exemption"
    PAYLOAD_VALIDATION = "payload-validation"


class Action(StrEnum):
    SUBSTITUTE = "substitute"
    ASK = "ask"
    BLOCK = "block"


class IntegrationMode(StrEnum):
    API_ANTHROPIC = "api-anthropic"
    API_RESPONSES = "api-responses"
    API_CHAT_COMPLETIONS = "api-chat-completions"
    WORKBENCH = "workbench"
    HOOK = "hook"


class ContentKind(StrEnum):
    TEXT = "text"
    JSON_STRING = "json-string"
    JSON_KEY = "json-key"
    TOOL_ARGUMENT = "tool-argument"
    TOOL_RESULT = "tool-result"
    SYSTEM = "system"
    FILE_TEXT = "file-text"


@dataclass(frozen=True, slots=True)
class Span:
    """Require 0 <= start < end <= the owning document length."""

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Finding:
    """Preserve source provenance and all mapped labels, not only a primary one.

    Detector spans address normalised input. Before source substitution, use the
    conservative offset map. A checksum failure alone does not clear a finding.
    """

    span: Span
    labels: frozenset[Label]
    primary_label: Label
    detector_id: str
    raw_label: str | None = None
    checksum_valid: bool | None = None
    statement_span: Span | None = None


@dataclass(frozen=True, slots=True)
class StageKey:
    """Identify one required stage on one payload content surface."""

    stage_id: str
    block_path: ContentPath


@dataclass(frozen=True, slots=True)
class StageStatus:
    """Distinguish a complete empty result from failure or missing evidence.

    Counts describe raw-stage evidence for disagreement checks. They are not
    derived from the allow-listed final projection.
    """

    stage_id: str
    state: StageState
    finding_count: int = 0
    person_count: int = 0
    document_sensitivity: float | None = None
    values_not_found: int = 0
    finish_reason: str | None = None
    artefact_manifest_id: str | None = None
    reason: ReasonCode | None = None


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Append evidence without values or mutable replacement of prior entries.

    Sequence numbers are strictly increasing. Exemption and derived findings
    refer to earlier entries. Mandatory actions are never cancelled.
    """

    sequence: int
    kind: LedgerKind
    block_path: ContentPath
    finding: Finding | None = None
    stage_status: StageStatus | None = None
    action: Action | None = None
    reason: ReasonCode | None = None
    mandatory: bool = False
    source_span: Span | None = None
    parent_sequences: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: Decision
    reasons: tuple[ReasonCode, ...]


@dataclass(frozen=True, slots=True)
class ContentLeaf:
    """A locally held textual surface; paths are not audit fields."""

    path: ContentPath
    text: str = field(repr=False)
    kind: ContentKind = ContentKind.TEXT


@dataclass(frozen=True, slots=True)
class RedactedText:
    """Gate-produced text eligible for later release validation, not authority."""

    text: str = field(repr=False)
    revision_id: str


@dataclass(frozen=True, slots=True)
class LocalText:
    """Restored or original local text; never accepted as upstream-ready text."""

    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RedactedPayload:
    """Immutable serialised candidate held by the gate until final validation."""

    body: bytes = field(repr=False)
    revision_id: str


@dataclass(frozen=True, slots=True)
class LocalPayload:
    """Locally restored API output or tool input, not cloud canonical history."""

    body: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """A parsed SSE event; raw data stays out of logs and audit."""

    event_type: str
    data: str = field(repr=False)
    event_id: str | None = None
