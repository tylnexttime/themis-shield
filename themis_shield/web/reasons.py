"""Authoritative, value-free plain-English reason catalogue.

Enum values are read from the installed contract, not guessed in JavaScript.
"""

from themis_shield.detectors.escalation import (
    ATTACHMENT, DOCUMENT_SENSITIVE, INJECTION_SIGNAL, LONG_TEXT, PII_FOUND, THOROUGH,
)
from themis_shield.register.models import REASONS, MARKING_REASONS
from themis_shield.register.two_stage import STAGE_REASONS
from themis_shield.register.gate_records import GATE_RECORD_REASONS
from themis_shield.register.review_records import GATE_REASON, REVIEW_REASONS, TRIPWIRE_REASON
from themis_shield.routing import RouteReasonCode
from themis_shield.types import ReasonCode


FIXED = {
    "ROUTE_LOCAL_MARKING": "The marking or security caveat requires local-only processing.",
    "ROUTE_SENSITIVE_LOCAL_OR_CONFIRM": "Choose local processing, or confirm the finished de-identified cloud package.",
    "ROUTE_CLOUD_HANDLING_ASK": "The information management marker requires confirmation before cloud use.",
    "ROUTE_CLOUD_PII": "Cloud is a candidate only after de-identification, policy checks and job approval.",
    "ROUTE_LOCAL_POLICY": "The use-case policy requires local-only processing.",
    "INVALID_CONVERSATION": "The conversation identifier is invalid.",
    "REGISTER_INTAKE_CONTRACT": "The register did not return a valid logged job.",
    "ROUTER_RECORD_CAPACITY": "The router's bounded job cache is full.",
    "DETECTION_UNAVAILABLE": "Required local detection could not be completed.",
    "DETECTION_TIMEOUT": "The overall local detection deadline elapsed. Release is blocked.",
    "GATE_BUSY": "Detection could not start within its admission deadline. Release is blocked.",
    "POLICY_BLOCK": "The technical policy blocked release.",
    "CANONICAL_HISTORY_NEEDS_REASSESSMENT": "Previously tokenised history needs a fresh assessment.",
    "PII_IN_PROTOCOL_FIELD": "Personal information was found in a protocol field that cannot be rewritten.",
    "TOOL_PROTOCOL": "The returned tool-call protocol is invalid.",
    "CLOUD_INCOMPLETE": "The cloud answer did not complete successfully.",
    "TOOL_NOT_ALLOWED": "The returned tool is not authorised.",
    "TOOL_TOKEN_NOT_STRICT": "A tool argument contains a non-strict placeholder.",
    "RESTORATION_OR_REQUIRED_STAGE_FAILED": "Restoration or another required answer stage failed.",
    "REGISTER_UNAVAILABLE": "The authoritative register could not be verified.",
    "INVALID_REQUEST": "The request is invalid or exceeds the local bound.",
    "CONVERSATION_CAPACITY": "The bounded conversation store is full.",
    "CONVERSATION_BUSY": "Another request is using this conversation.",
    "JOB_ALREADY_COMPLETED": "This job is complete; new work needs a new request.",
    "APPROVAL_NOT_ACTIVE": "This job is closed and has no active permission to send.",
    "LOCAL_AUTHORITY_REQUIRED": "Authorised local processing is required.",
    "SPARK_NOT_CONFIGURED": "The DGX Spark target is not configured; there is no cloud fallback.",
    "LOCAL_OUTPUT_INCOMPLETE": "The local answer did not complete successfully.",
    "DESTINATION_NOT_AUTHORISED": "The requested cloud destination is not authorised for this use case.",
    "PACKAGE_CHANGED": "The clean package differs from the bound package; reassessment is required.",
    "REQUESTER_CONFIRM_REQUIRED": "The requester must confirm the finished clean package.",
    "OPERATOR_APPROVAL_REQUIRED": "Both operator approval stages must be complete before sending.",
    "APPROVED_TOKENISED": "The approved, bound package was sent with placeholders.",
    "UNSUPPORTED_OR_UNSAFE_CONTENT": "The content is unsupported or cannot safely be fully inspected.",
    "REQUIRED_STAGE_FAILED": "A required processing stage failed; release is blocked.",
    "LOOPBACK_REQUIRED": "Only a connection from this computer is permitted.",
    "LOOPBACK_HOST_REQUIRED": "The local gate host header is required.",
    "ORIGIN_DENIED": "The browser origin is not permitted.",
    "LOCAL_BEARER_REQUIRED": "A valid local session or bearer credential is required.",
    "OPERATOR_CREDENTIAL_REQUIRED": (
        "Operator actions need the Operator's own credential (operator-token, or a browser session opened with "
        "`themis serve --open-operator`); the client token and the client session are refused."
    ),
    "CONTENT_ENCODING_UNSUPPORTED": "Encoded request bodies are not supported.",
    "FRONT_DOOR_FAILED": "The local API front door failed closed.",
    "CALIBRATION_MAINTENANCE": "Calibration or maintenance currently excludes detection.",
    "NOT_FOUND_OR_EXPIRED": "The requested local item is missing or expired.",
    "OPERATION_REFUSED": "The controlled local operation was refused.",
    "INVALID_OR_UNSAFE_REQUEST": "The local request is invalid or unsafe.",
    "REQUIRED_SERVICE_UNAVAILABLE": "A required local service is unavailable.",
    "LAUNCHER_CODE_INVALID": "The launcher code is invalid, expired or already used.",
    "RUNTIME_UNAVAILABLE": "Local runtime status could not be read.",
    "INVALID_PREVIEW": "The preview request is invalid.",
    "PREVIEW_EXPIRED": "The preview has expired; check again.",
    "INVALID_CHAT_OR_PREVIEW_BINDING": "The chat does not exactly match its preview.",
    "CHAT_FAILED": "Chat processing failed; check the register before retrying.",
    "INVALID_JSON": "The request is not valid supported JSON.",
    "OPERATOR_CONSOLE_DISABLED": "The Operator console is disabled.",
    "INVALID_OPERATOR_ACTION": "The operator action is invalid.",
    "INVALID_HANDOVER": "The handover request is invalid.",
    "INVALID_PEER_CHECK": "The peer-check request is invalid.",
    "INVALID_SETTINGS": "The settings request is invalid.",
    "INVALID_CALIBRATION": "The calibration request is invalid.",
    "CALIBRATION_JOB_CAPACITY": "The bounded calibration job store is full.",
    "CALIBRATION_FAILED": "Calibration failed; no measurement was recorded.",
    "CALIBRATION_NOT_FOUND": "The calibration job is not available.",
    "HOOK_OPERATION_FAILED": "The hook operation failed closed.",
    "VOLUME": "The job exceeds the routine document or word limit.",
    "LONG_DURATION": "The requested duration exceeds the routine limit.",
    "REPEAT_SIGNAL": "Repeated overrides or refusals require major-job review.",
    "NEW_SOURCE": "A new data source requires major-job review.",
    "NEW_CONFIGURATION": "A new cloud configuration requires major-job review.",
    "BATCH": "Scheduled or repeated batch work requires major-job review.",
    "CONTRACT_SCOPE": "Restricted-contract scope requires major-job review.",
    "NO_ENDORSEMENT": "No matching current use-case endorsement is available.",
    "STATE_CHANGE": "The work changes register state and requires major-job review.",
    "SENSITIVE_STATEMENT": "Sensitive statements require major-job review.",
    "ASSESSED": "The marking has been assessed.",
    "UNMARKED": "No protective marking was recognised.",
    "UNRESOLVED": "The marking is unresolved; release is not authorised.",
    "FINISHED": "Processing finished.",
    "NO_OUTPUT": "Processing closed without output.",
    "STOPPED": "Processing was stopped.",
    "CHECKED": "The clean package was peer-checked.",
    "REVIEW_REQUIRED": "Further peer review is required.",
    "KNOWN_MISS_HIT": "The de-identified text matches the known-miss register; check the listed findings, then confirm.",
    "KNOWN_MISS_STALE": "An imported known-miss snapshot is older than the configured limit; a person checks the text, or an Operator re-imports a current export.",
    "TRIPWIRE_UNAVAILABLE": "The cloud trip-wire could not be completed; the job stopped (fail closed).",
    "TRIPWIRE_FLAGGED": (
        "The cloud trip-wire flagged possible residual personal information in the approved package. Nothing was "
        "replaced; the job went back to the Operator to re-approve the same package or refuse it."
    ),
    "TRIPWIRE_REVIEW_REQUIRED": "An Operator must re-approve or refuse the flagged package before this job can continue.",
    "TRIPWIRE_NOT_CONVERGED": (
        "NOT CONVERGED: the cloud trip-wire still reported personal information after its last correction round. "
        "The Operator checks the listed rounds and the package before approving or refusing it."
    ),
    "TRIPWIRE_NOT_RUN": (
        "This package was prepared before the cloud trip-wire was switched on, so it was never checked; start the "
        "work again as a new request."
    ),
    "REVIEW_UNAVAILABLE": (
        "The highlighted text of this job is not in this gate's memory (the gate restarted or the conversation "
        "expired); the requester sends again to rebuild it."
    ),
    "REVIEW_REBUILD_REQUIRED": (
        "An undo removed a placeholder from this job's package, so the highlighted text shown was out of date. The "
        "requester sends again to rebuild the package; then settle the point on the new text."
    ),
    "INVALID_REVIEW_ACTION": "The review action is invalid.",
    "INVALID_REPLACEMENT_SPAN": (
        "The chosen span must cover the point, keep whole placeholders and not be empty or blank."
    ),
    "OPERATOR_REPLACEMENT_NOT_APPLIED": (
        "A placeholder the Operator added did not appear in the rebuilt package; the job stopped (fail closed)."
    ),
    "POINT_NOT_SETTLED": "This point is not settled, so there is nothing to undo.",
    "UNDO_UNAVAILABLE": (
        "This replacement was made before the gate restarted, so the placeholder it added can no longer be "
        "identified and it cannot be undone. Stop the document, or approve it as it stands."
    ),
    "PLACEHOLDER_IN_USE": (
        "The placeholder this replacement added is already part of what was sent, or of another job's package, so "
        "it is kept and the replacement cannot be undone."
    ),
    "REVIEW_CLOSED": "This job is closed (stopped, refused or completed), so its originals are no longer shown.",
    "PLACEHOLDER_SHARED": (
        "Another replacement in this job reuses the placeholder this one added (the same value was replaced there "
        "too). Undo that replacement first, then this one."
    ),
    "REPLACEMENT_TOO_SHORT": (
        "The text to replace is too short or too common a word to keep in the codebook (its letters occur in ordinary "
        "words, so the cloud answer would be refused). Widen the span to take in a neighbouring word, or settle the "
        "point another way."
    ),
    "REPLACEMENT_NOT_ACCEPTED": (
        "This span holds a placeholder, and it cannot be replaced with that category. So as to say nothing about the "
        "hidden original, the gate does not say why. Choose the placeholder's own category, widen the span to take in "
        "a neighbouring word, or settle the point another way."
    ),
    "REPLACEMENT_CATEGORY_WEAKER": (
        "The chosen category is weaker than a placeholder inside the span. Pick that placeholder's category or a "
        "stronger one."
    ),
    "TRIPWIRE_NOT_PENDING": (
        "Nothing is waiting for a trip-wire decision on this job: re-approve and refuse apply only to a package the "
        "check after approval sent back."
    ),
}
POLICY = {
    "STAGE_FAILED": "A required detection stage failed.",
    "STAGE_TIMEOUT": "A required detection stage timed out.",
    "MALFORMED_OUTPUT": "Required detector output is malformed.",
    "INCOMPLETE_OUTPUT": "Required detector output is incomplete.",
    "UNMATCHED_LLM_VALUES": "A local finder reported several values that are not in the text word for word. Check the de-identified text before sending.",
    "STAGE_MISSING": "A required detection stage is missing.",
    "SECRET": "A secret finding blocks release.",
    "SENSITIVE": "Sensitive information requires confirmation and review.",
    "DISAGREEMENT_EMPTY_LLM": "The finders returned no findings despite contrary tracer evidence.",
    "DISAGREEMENT_FEW_PERSONS": "The finders found substantially fewer people than the tracer.",
    "QUASI_ID": "A detail that can single someone out without naming them (a quasi-identifier) requires confirmation and review.",
    "INJECTION_SIGNAL": "Possible instruction injection requires confirmation and review.",
    "KNOWN_MISS_HIT": "The de-identified text matches an entry in the known-miss register; check the finding before confirming.",
    "KNOWN_MISS_STALE": "An imported known-miss snapshot is older than the configured limit; the job needs a human check.",
    "KNOWN_MISS_UNREADABLE": "The known-miss register could not be read; release is blocked.",
    "KNOWN_MISS_DEADLINE": "The known-miss sweep did not finish within its deadline; a person checks the text before it is sent.",
    "TYPED_INDIRECT": "A sentence no detector flagged may reveal a sensitive fact about a person; check it before confirming.",
}


# Operator review: one plain-English line per held point, from the hold reason and a label. Fixed text only; the
# point's value is never part of it.
TOPICS = {
    "HEALTH": "someone's health", "DISABILITY": "someone's disability",
    "INDIGENOUS": "whether someone is Aboriginal or Torres Strait Islander",
    "SEXUALITY": "someone's sexuality", "RELIGION": "someone's religion",
    "CRIMINAL": "someone's criminal record or offending", "FAMILY_VIOLENCE": "family violence involving someone",
    "ETHNICITY": "someone's ethnicity", "UNION": "someone's trade union membership",
}
VALUE_KINDS = {
    "PERSON": "a name", "EMAIL": "an email address", "PHONE": "a phone number", "ADDRESS": "an address",
    "DATE_OF_BIRTH": "a date of birth", "OTHER_DATE": "a date", "URL_PERSONAL": "a personal web address",
    "USERNAME": "a username", "QUASI_ID": "a detail that could single someone out",
    "PAYMENT_CARD": "a payment card number", "BANK_ACCOUNT": "a bank account number",
}
POINT_REASONS = {
    "ASK:quasi-identifier": "a detail that could single someone out without naming them",
    "ASK:disagreement-few-persons": (
        "the detectors disagreed on whether this is a name (one found far more people than the others)"
    ),
    "ASK:injection-signal": "words that look like instructions to the AI model",
    "ASK:typed-indirect": "a sentence that may reveal a sensitive fact about someone",
    "ASK:unmatched-llm-values": (
        "a finder reported values that are not in the text word for word: read the whole text"
    ),
    "ASK:known-miss-hit": "the text matches an entry in the known-miss register",
    "TRIPWIRE_UNREPLACED": (
        "a short or common-word name the cloud trip-wire flagged; the gate never replaces such a word by itself, "
        "so you decide"
    ),
    "ASK:known-miss-stale": "the known-miss register snapshot is out of date: read the whole text",
    "ASK:known-miss-deadline": "the known-miss check did not finish in time: read the whole text",
    "ASK:ambiguous-entity": "the gate could not tell which person or thing this refers to",
}


def value_kind(label):
    if label in TOPICS:
        return "a statement about " + TOPICS[label]
    if label in VALUE_KINDS:
        return VALUE_KINDS[label]
    return "an identifier" if label else "personal information"


def point_reason(reason, label):
    """The one-line reason shown beside a highlighted point (category and why), from fixed text only."""
    if reason == "ASK:sensitive":
        return "a statement about " + TOPICS.get(label, "a sensitive matter concerning someone")
    if reason == "ASK:disagreement-empty-llm":
        return "the detectors disagreed on whether this is " + VALUE_KINDS.get(label, "personal information")
    if reason == GATE_REASON:
        return value_kind(label) + ", replaced by the gate"
    if reason == TRIPWIRE_REASON:
        return value_kind(label) + ", replaced by the cloud trip-wire's correction loop"
    if reason in POINT_REASONS:
        return POINT_REASONS[reason]
    item = next((code for code in ReasonCode if code.value == reason), None)
    return POLICY.get(item.name, "a technical hold") if item is not None else "a technical hold"


def catalogue():
    result = {**FIXED, **REASONS, **STAGE_REASONS, **GATE_RECORD_REASONS, **REVIEW_REASONS}
    result.update({
        PII_FOUND: "Tier-1 personal findings require both finder tiers.",
        DOCUMENT_SENSITIVE: "The document sensitivity score requires both finder tiers.",
        LONG_TEXT: "The text length requires both finder tiers.",
        ATTACHMENT: "Attachments require both finder tiers.",
        INJECTION_SIGNAL: "Possible instruction injection requires both finder tiers.",
        THOROUGH: "A thorough check requires both finder tiers.",
    })
    for item in RouteReasonCode:
        result[item.value] = FIXED[item.name]
    for item in ReasonCode:
        # Every installed enum member gets readable text. Named technical
        # reasons have explicit explanations, including BLOCK:/ASK: values.
        result[item.value] = POLICY.get(
            item.name, "Technical policy reason: " + item.name.replace("_", " ").lower() + ".",
        )
    for code in MARKING_REASONS:
        result.setdefault(code, FIXED.get(code, code.replace("_", " ").capitalize() + "."))
    return result
