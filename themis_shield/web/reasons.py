"""Authoritative, value-free plain-English reason catalogue.

Enum values are read from the installed contract, not guessed in JavaScript.
"""

from themis_shield.detectors.escalation import (
    ATTACHMENT, DOCUMENT_SENSITIVE, INJECTION_SIGNAL, LONG_TEXT, PII_FOUND, THOROUGH,
)
from themis_shield.register.models import REASONS, MARKING_REASONS
from themis_shield.register.two_stage import STAGE_REASONS
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
}


def catalogue():
    result = {**FIXED, **REASONS, **STAGE_REASONS}
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
