"""Operator review of a held job, point by point (where each hold came from, and the Operator's verdict on it).

Three end states, and ASK is only a waypoint: every document ends STOPPED (a local-only marking, an Operator refusal or
an Operator stop), DE-IDENTIFIED AND RELEASED, or RELEASED AS-IS (nothing personal or sensitive was found).

How it fits the request path:

1. ``Router._protect`` builds a *draft* (``draft`` below) from the decision evidence: the de-identified text as the
   Operator reads it (each content surface's substituted text, joined by blank lines) and one point per hold trigger,
   with its fixed hold reason, a label and its position in that text. Every placeholder the gate put in without a
   hold is a point "settled by the gate". The draft holds no original value: placeholders stand where values were.
2. ``router_flow.handle`` records the points, value-free, in the register (``register/review_records.py``) for the
   bound clean package, and keeps the draft in memory (``ReviewState``) with the conversation whose vault holds the
   originals. Nothing of the draft is written anywhere else.
3. The Operator, with the Operator credential only (``/api/operator/*``), sees the text with each point highlighted
   and a one-line reason, may reveal the original of a point (``reveal``: read from the vault on request, returned to
   that request only, never recorded), and settles each point: ``not-personal`` (leave it as shown), ``replace``
   (a placeholder the Operator adds to the job's codebook, marked ``de-identified-by-operator``; the clean package
   is rebuilt on the next send) or stop (the job is refused).
4. No stage is approved while a point is unsettled (the register enforces it for every approval path).

Learning rule: nothing an Operator does in one job changes the gate. A replacement lives in that job's codebook
only; "not personal" verdicts are counted per hold reason (``themis operator stats``) and become false-positive
catalogue entries only when someone deliberately promotes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re

from .detectors.escalation import INJECTION
from .placeholders import (
    SENSITIVE_LABELS, STRICT_TOKEN_PATTERN, TokenAllocator, TokenClass, escape_literals, parse_strict_token,
    unescape_literals,
)
from .register.review_records import CATEGORIES, GATE_REASON
from .types import Decision, Label, ReasonCode


TOKEN = re.compile(STRICT_TOKEN_PATTERN)
CATEGORY_CLASS = {
    "person": TokenClass.PERSON, "contact": TokenClass.CONTACT, "address": TokenClass.ADDRESS,
    "identifier": TokenClass.IDENTIFIER, "date": TokenClass.DATE, "statement": TokenClass.STATEMENT,
    "quasi-identifier": TokenClass.QUASI_IDENTIFIER,
}
CLASS_CATEGORY = {value: key for key, value in CATEGORY_CLASS.items()}
assert set(CATEGORY_CLASS) == set(CATEGORIES)
MAX_REPLACEMENT = 4096


@dataclass
class ReviewState:
    """One held job's review, in memory only: the de-identified text, its points and the conversation whose vault
    holds the originals. Never persisted; lost on restart (the requester re-sends to rebuild it)."""

    conversation_id: str = field(repr=False)
    manifest_hash: str
    text: str = field(repr=False)
    points: list = field(repr=False)
    tokens: dict = field(repr=False)


def point_id(reason, label, start, end):
    """Stable across re-sends of the same request: positions in the scanned original text, never its content."""
    raw = f"{reason}|{label or ''}|{start}|{end}".encode()
    return "pt-" + hashlib.sha256(raw).hexdigest()[:12]


# -- the draft ----------------------------------------------------------------------------------------------------


class _Surface:
    """One content surface: where it starts in the scanned text and in the review text, and its segments."""

    def __init__(self, scan_start, text, protected, review_start):
        self.scan_start, self.text, self.review_start = scan_start, text, review_start
        self.segments = []  # (kind, orig_start, orig_end, review_start, review_end, replacement)
        if protected is None:
            self.review_text = text
            self.segments.append(("plain", 0, len(text), review_start, review_start + len(text), None))
            return
        self.review_text = protected.tokenised_text
        cursor, position = 0, review_start
        for item in protected.replacements:
            start, end = item.original_span.start, item.original_span.end
            plain = escape_literals(text[cursor:start])
            self.segments.append(("plain", cursor, start, position, position + len(plain), None))
            position += len(plain)
            self.segments.append(("token", start, end, position, position + len(item.token), item))
            position += len(item.token)
            cursor = end
        plain = escape_literals(text[cursor:])
        self.segments.append(("plain", cursor, len(text), position, position + len(plain), None))
        if position + len(plain) - review_start != len(self.review_text):
            # Never guess a position: a surface whose layout cannot be reproduced exactly is reviewed as a whole.
            self.segments = None

    def contains(self, start):
        return self.scan_start <= start < self.scan_start + max(1, len(self.text))

    def locate(self, start, end):
        """Review-text span, kind and token for a scanned span inside this surface (None: not locatable)."""
        if self.segments is None:
            return None
        a, b = start - self.scan_start, min(end - self.scan_start, len(self.text))
        touched = [item for item in self.segments if item[1] < b and item[2] > a and item[1] < item[2]]
        if not touched:
            return None
        low, high = [], []
        for kind, orig_start, orig_end, review_start, review_end, _ in touched:
            if kind == "token":
                low.append(review_start)
                high.append(review_end)
            else:
                low.append(review_start + len(escape_literals(self.text[orig_start:max(a, orig_start)])))
                high.append(review_start + len(escape_literals(self.text[orig_start:min(b, orig_end)])))
        tokens = [item for item in touched if item[0] == "token"]
        if len(touched) == 1 and tokens:
            return min(low), max(high), "placeholder", tokens[0][5].token
        return min(low), max(high), "text", None


def _triggers(reason, evidence, scanned):
    """(scanned start, end, label) for each place a hold reason came from; empty means the whole document."""
    from .detectors.engine import LLM_STAGES
    from .policy import contrary_tracer_findings, sensitive_findings, typed_only
    findings = evidence.findings
    if reason == ReasonCode.SENSITIVE:
        return [
            (item.start, item.end, sorted(label.value for label in item.labels & SENSITIVE_LABELS)[0])
            for item in sensitive_findings(evidence)
        ]
    if reason == ReasonCode.QUASI_ID:
        return [(item.start, item.end, Label.QUASI_ID.value) for item in findings if Label.QUASI_ID in item.labels]
    if reason == ReasonCode.DISAGREEMENT_EMPTY_LLM:
        return [
            (item.start, item.end, Label.PERSON.value if Label.PERSON in labels else sorted(x.value for x in labels)[0])
            for item, labels in contrary_tracer_findings(evidence)
        ]
    if reason == ReasonCode.DISAGREEMENT_FEW_PERSONS:
        return [
            (item.start, item.end, Label.PERSON.value) for item in findings
            if any(entry.stage == "pii-tracer" and entry.label == Label.PERSON for entry in item.evidence)
            and not any(entry.stage in LLM_STAGES for entry in item.evidence)
        ]
    if reason == ReasonCode.TYPED_INDIRECT:
        return [(item.start, item.end, item.primary_label.value) for item in findings if typed_only(item)]
    if reason == ReasonCode.INJECTION_SIGNAL:
        return [(match.start(), match.end(), None) for match in INJECTION.finditer(scanned) if match.end() > match.start()]
    return []


def draft(scanned, starts, surfaces, results, evidence, decision):
    """Build the review draft. ``results[i]`` is surface i's SubstitutionResult, or None when its text went out as
    it was (canonical history, an immutable field without findings)."""
    layout, position = [], 0
    for start, surface, result in zip(starts, surfaces, results):
        item = _Surface(start, surface.text, result, position)
        layout.append(item)
        position += len(item.review_text) + 2
    text = "\n\n".join(item.review_text for item in layout)
    points, tokens, covered = [], {}, set()

    def locate(start, end):
        surface = next((item for item in layout if item.contains(start)), None)
        return surface.locate(start, end) if surface is not None else None

    reasons = [item for item in getattr(decision, "reasons", ()) if item.value.startswith("ASK:")]
    if getattr(decision, "decision", None) == Decision.ASK:
        for reason in reasons:
            found = []
            for start, end, label in _triggers(reason, evidence, scanned):
                place = locate(start, end)
                if place is not None:
                    found.append((start, end, label, place))
            seen = set()
            for start, end, label, (review_start, review_end, kind, token) in found:
                key = (review_start, review_end, label)
                if key in seen:
                    continue  # one point per highlighted place and label (merged findings share one placeholder)
                seen.add(key)
                identifier = point_id(reason.value, label, start, end)
                points.append({
                    "id": identifier, "reason": reason.value, "label": label, "kind": kind,
                    "start": review_start, "end": review_end, "settled_by": "operator",
                })
                if token is not None:
                    tokens[identifier] = token
                    covered.add(token)
            if not seen:
                points.append({
                    "id": point_id(reason.value, None, -1, -1), "reason": reason.value, "label": None,
                    "kind": "document", "start": None, "end": None, "settled_by": "operator",
                })
    for surface in layout:
        for kind, orig_start, _, review_start, review_end, replacement in surface.segments or ():
            if kind != "token" or replacement.token in covered:
                continue
            identifier = point_id(GATE_REASON, replacement.label.value, surface.scan_start + orig_start, review_start)
            points.append({
                "id": identifier, "reason": GATE_REASON, "label": replacement.label.value, "kind": "placeholder",
                "start": review_start, "end": review_end, "settled_by": "gate",
            })
            tokens[identifier] = replacement.token
    unique, ids = [], set()
    for point in points:
        if point["id"] not in ids:
            ids.add(point["id"])
            unique.append(point)
    return {"text": text, "points": unique, "tokens": tokens}


# -- the Operator's view and actions ---------------------------------------------------------------------------


def location(text, start):
    if start is None or text is None:
        return "whole document"
    prefix = text[:start]
    paragraph = len(re.findall(r"\n[ \t]*\n", prefix)) + 1
    return f"paragraph {paragraph}, line {prefix.count(chr(10)) + 1}"


def end_state(row):
    """STOPPED, DE-IDENTIFIED AND RELEASED, RELEASED AS-IS, or None while the job is still open (ASK, a waypoint)."""
    import json
    if row["route"] == "LOCAL_ONLY" or row["status"] in {"Refused", "Withdrawn", "Expired"}:
        return "stopped"
    if row["status"] == "Completed":
        counts = json.loads(row["pii_counts_json"] or "{}")
        return "de-identified-and-released" if counts else "released-as-is"
    return None


def _state(router, job_id):
    state = getattr(router, "_reviews", {}).get(job_id)
    if state is None:
        from .router import GateFailure
        raise GateFailure("REVIEW_UNAVAILABLE", 404)
    return state


def view(router, job_id):
    """Value-free: the de-identified text, the points with reasons and locations, and how each was settled."""
    from .web.reasons import point_reason
    row = router.register.get_details(job_id)
    recorded = router.register.review_state(job_id)
    state = getattr(router, "_reviews", {}).get(job_id)
    if state is not None and state.manifest_hash != recorded["manifest_hash"]:
        state = None
    text = state.text if state is not None else None
    held, gate = [], []
    for number, point in enumerate(item for item in recorded["points"] if item["settled_by"] == "operator"):
        resolution = point["resolution"]
        held.append({
            "id": point["id"], "number": number + 1, "reason": point["reason"], "label": point["label"],
            "kind": point["kind"], "start": point["start"], "end": point["end"],
            "location": location(text, point["start"]), "why": point_reason(point["reason"], point["label"]),
            "placeholder": state.tokens.get(point["id"]) if state is not None else None,
            "category": (
                CLASS_CATEGORY[parse_strict_token(state.tokens[point["id"]]).token_class]
                if state is not None and point["id"] in state.tokens else None
            ),
            "resolution": None if resolution is None else {
                "action": resolution["action"], "category": resolution["category"],
                "start": resolution["start"], "end": resolution["end"],
                "by": resolution["identity"], "role": resolution["role"], "at": resolution["at"],
            },
        })
    for point in recorded["points"]:
        if point["settled_by"] == "gate":
            gate.append({
                "label": point["label"], "start": point["start"], "end": point["end"],
                "location": location(text, point["start"]), "why": point_reason(point["reason"], point["label"]),
            })
    return {
        "job_id": job_id, "manifest_hash": recorded["manifest_hash"], "package": row["package"],
        "status": row["status"], "end_state": end_state(row), "text": text,
        "text_available": text is not None, "points": held, "gate_settled": gate,
        "unresolved": len(recorded["unresolved"]), "total": len(held), "stopped": recorded["stopped"],
        "categories": list(CATEGORIES),
    }


def _held(state, point_id):
    point = next((item for item in state.points if item["id"] == point_id and item["settled_by"] == "operator"), None)
    if point is None:
        raise KeyError("unknown point")
    return point


def _original(router, state, start, end):
    """The original behind a review-text span: placeholders read from the vault, plain text unescaped."""
    segment = state.text[start:end]
    mapping = router.vault.mapping(state.conversation_id)
    parts, cursor = [], 0
    for match in TOKEN.finditer(segment):
        parts.append(unescape_literals(segment[cursor:match.start()]))
        if match.group() not in mapping:
            raise KeyError("the original is no longer held")
        parts.append(mapping[match.group()])
        cursor = match.end()
    parts.append(unescape_literals(segment[cursor:]))
    return "".join(parts)


def reveal(router, job_id, point_id):
    """The original text of one held point, for the signed-in Operator's screen only.

    Read from the vault on request and returned to this request only: no register row, audit row or log line is
    written, and the response is marked no-store by the app.
    """
    state = _state(router, job_id)
    if state.manifest_hash != router.register.get_details(job_id)["manifest_hash"]:
        raise KeyError("the clean package changed")
    point = _held(state, point_id)
    if point["kind"] == "document":
        raise ValueError("a whole-document point has no single place to reveal")
    return {"point_id": point_id, "original": _original(router, state, point["start"], point["end"])}


def _span_ok(state, point, start, end):
    text = state.text
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        return False
    if point["kind"] != "document" and not (start < point["end"] and end > point["start"]):
        return False  # the adjusted span still covers the point
    for match in TOKEN.finditer(text):
        if match.start() < end and match.end() > start and not (start <= match.start() and match.end() <= end):
            return False  # never cut through a placeholder
    parts = TOKEN.split(text[start:end])
    if any("⟦" in part or "⟧" in part or escape_literals(unescape_literals(part)) != part for part in parts):
        return False  # plain text must read back exactly as it was written
    if "\n\n" in text[start:end]:
        return False  # one content surface at a time
    return bool(text[start:end].strip())


def replace(router, job_id, point_id, start, end, category, *, actor):
    """"Replace it": the span (confirmed or adjusted) becomes a placeholder in this job's codebook, marked
    ``de-identified-by-operator``. The clean package is rebuilt and re-bound on the next send; until then no stage
    two approval is possible. A span that is exactly the gate's own placeholder of the same category is confirmed
    as it is (nothing changes)."""
    from .router import GateFailure
    from .vault import OPERATOR_SOURCE
    state = _state(router, job_id)
    row = router.register.get_details(job_id)
    if state.manifest_hash != row["manifest_hash"]:
        raise KeyError("the clean package changed")
    point = _held(state, point_id)
    if category not in CATEGORY_CLASS or not _span_ok(state, point, start, end):
        raise GateFailure("INVALID_REPLACEMENT_SPAN", 400)
    token_class = CATEGORY_CLASS[category]
    segment = state.text[start:end]
    whole = TOKEN.fullmatch(segment)
    if whole is not None and parse_strict_token(segment).token_class == token_class:
        reopen = False
    else:
        original = _original(router, state, start, end)
        if len(original) > MAX_REPLACEMENT or not original.strip():
            raise GateFailure("INVALID_REPLACEMENT_SPAN", 400)
        try:
            token = TokenAllocator(state.conversation_id, router.vault).allocate(original, token_class)
        except Exception:
            # The same value already has a placeholder of another category in this conversation.
            raise GateFailure("INVALID_REPLACEMENT_SPAN", 400) from None
        router.vault.mark_source(state.conversation_id, token, OPERATOR_SOURCE)
        with router._state_lock:
            router._operator_tokens.setdefault(job_id, set()).add(token)
        reopen = True
    router.register.resolve_point(
        job_id, point_id, "replaced", actor_identity=actor, manifest_hash=state.manifest_hash,
        category=category, start=start, end=end, reopen=reopen, now=router.clock(),
    )
    return view(router, job_id)


def not_personal(router, job_id, point_id, *, actor):
    """"Not personal: leave it": the text stays as shown (a placeholder the gate already put in stays a placeholder;
    this never sends an original). Recorded and counted per hold reason; never promoted automatically."""
    router.register.resolve_point(job_id, point_id, "not-personal", actor_identity=actor, now=router.clock())
    return view(router, job_id)


def stop(router, job_id, point_id, *, actor):
    router.register.stop_document(job_id, point_id, actor_identity=actor, now=router.clock())
    return view(router, job_id)
