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

5. Undo (``undo``): until stage two of the package is approved, the Operator may undo a "not personal" or "replace"
   settlement. The undo is recorded (who, when) and the point is open again; undoing a replacement removes its
   placeholder from the job's codebook, so the next send rebuilds the package without it.

With the cloud trip-wire's correction loop on (``[tripwire] before_human``), the placeholders the loop added are
points settled without a person (reason ``REPLACED_BY_TRIPWIRE``, shown with their round), never new open points:
the gate's own hold points stay the only ones an Operator must settle. ``view`` also carries the trip-wire's per-round
list for the job (what each round flagged and added: categories, counts, placeholder ids, never values) and, for a
package the check after approval (``after_human``) sent back, its findings and whether the Operator's re-approve or
refuse is pending (``tripwire_verdict``). A finding that was not replaced is highlighted only when its character span
in the checked package maps exactly onto one content surface of the review text; otherwise it is listed only.

Learning rule: nothing an Operator does in one job changes the gate. A replacement lives in that job's codebook
only; "not personal" verdicts are counted per hold reason (``themis operator stats``) and become false-positive
catalogue entries only when someone deliberately promotes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re

from .detectors.escalation import INJECTION
from .placeholders import (
    SENSITIVE_LABELS, STRICT_TOKEN_PATTERN, TokenAllocator, TokenClass, escape_literals, parse_strict_token,
    unescape_literals,
)
from .register.review_records import CATEGORIES, GATE_REASON, TRIPWIRE_REASON, UNDOABLE
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
    # (start, end) of each content surface in ``text``, and the checked package as the trip-wire reads it
    # (``catalogues.tripwire.document``): used only to place a trip-wire finding exactly, never stored.
    surfaces: tuple = field(default=(), repr=False)
    document: str | None = field(default=None, repr=False)


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
    surfaces = tuple((item.review_start, item.review_start + len(item.review_text)) for item in layout)
    return {"text": text, "points": unique, "tokens": tokens, "surfaces": surfaces}


def mark_tripwire(review, rounds):
    """Placeholders the trip-wire's correction loop added become points settled by it (``REPLACED_BY_TRIPWIRE``)
    instead of by the gate. A hold point of the gate that sits on such a placeholder stays an open point."""
    added = {token for item in rounds for token in item.get("added", ())}
    if not review or not added:
        return review
    for point in review["points"]:
        if point["reason"] == GATE_REASON and review["tokens"].get(point["id"]) in added:
            point["reason"] = TRIPWIRE_REASON
    return review


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
        _not_stale(router, job_id)
        raise GateFailure("REVIEW_UNAVAILABLE", 404)
    return state


def _not_stale(router, job_id):
    """FAB-03: after an undo removed a placeholder, the review text on record is that of a package that no longer
    exists; nothing is settled on it until the requester re-sends and the package is rebuilt."""
    from .router import GateFailure
    with router._state_lock:
        stale = job_id in getattr(router, "_review_stale", ())
    if stale:
        raise GateFailure("REVIEW_REBUILD_REQUIRED", 409)


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
    replaced_here = dict(getattr(router, "_operator_points", {}).get(job_id, {}))
    open_stage = row["status"] in {"Logged", "Awaiting-Operator", "Escalated-Review"} and row["stage2"] != "approved"
    for number, point in enumerate(item for item in recorded["points"] if item["settled_by"] == "operator"):
        resolution = point["resolution"]
        undo = (
            resolution is not None and resolution["action"] in UNDOABLE and open_stage
            and (resolution["action"] != "replaced" or point["id"] in replaced_here)
        )
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
            "undo": undo,
            "undone": [{
                "action": item["action"], "category": item["category"], "by": item["identity"], "at": item["at"],
                "undone_by": item["undone_by"], "undone_at": item["undone_at"],
            } for item in point.get("undone", ())],
        })
    trip = tripwire_view(router, job_id, row, state)
    rounds_of = {}
    for loop in (router.register.tripwire_loops(job_id) if hasattr(router.register, "tripwire_loops") else ()):
        for item in loop["rounds"]:
            for token in item.get("added", ()):
                rounds_of.setdefault(token, item["round"])
    for point in recorded["points"]:
        if point["settled_by"] == "gate":
            token = state.tokens.get(point["id"]) if state is not None else None
            tripwire = point["reason"] == TRIPWIRE_REASON
            gate.append({
                "label": point["label"], "start": point["start"], "end": point["end"],
                "location": location(text, point["start"]), "why": point_reason(point["reason"], point["label"]),
                "source": "tripwire" if tripwire else "gate",
                "round": rounds_of.get(token) if tripwire else None,
                "placeholder": token,
            })
    return {
        "job_id": job_id, "manifest_hash": recorded["manifest_hash"], "package": row["package"],
        "status": row["status"], "end_state": end_state(row), "text": text,
        "text_available": text is not None, "points": held, "gate_settled": gate,
        "unresolved": len(recorded["unresolved"]), "total": len(held), "stopped": recorded["stopped"],
        "categories": list(CATEGORIES), "tripwire": trip,
    }


# -- the cloud trip-wire in the review ------------------------------------------------------------------------


_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_UNIT = re.compile(r"\\u[0-9a-fA-F]{4}|\\.|[^\\]", re.S)


def flag_span(state, start, end):
    """Where a trip-wire finding's span ``[start, end)`` in the checked package lies in the review text, or None.

    Exact or nothing: the span must lie inside one JSON string value of the package (not a key), that value must be
    exactly the text of one content surface of the review (and of only one), and decoding it unit by unit must give
    the same text. A finding that fails any of these is listed, never highlighted at a guessed place."""
    document = state.document if state is not None else None
    if document is None or start is None or end is None:
        return None
    for literal in _LITERAL.finditer(document):
        inner_start, inner_end = literal.start() + 1, literal.end() - 1
        if inner_start > start:
            return None
        if inner_end < end:
            continue
        if document[literal.end():literal.end() + 1] == ":":
            return None
        try:
            value = json.loads(literal.group())
        except ValueError:
            return None
        decoded, low, high = 0, None, None
        for unit in _UNIT.finditer(document, inner_start, inner_end):
            if low is None and unit.end() > start:
                low = decoded
            try:
                decoded += len(json.loads('"' + unit.group() + '"'))
            except ValueError:
                return None
            if unit.start() < end:
                high = decoded
        if decoded != len(value) or low is None or high is None or low >= high:
            return None
        matches = [item for item in state.surfaces if state.text[item[0]:item[1]] == value]
        if len(matches) != 1:
            return None
        return matches[0][0] + low, matches[0][0] + high
    return None


def _finding(item, state, current):
    """One trip-wire finding, value-free: category, where (characters of the checked package, or next to a
    placeholder), what it added, its known-miss proposal, and its place in the review text when exact."""
    place = flag_span(state, item.get("start"), item.get("end")) if current else None
    return {
        "category": item.get("category"), "start": item.get("start"), "end": item.get("end"),
        "placeholder": item.get("placeholder"), "added": list(item.get("added") or ()),
        "proposal": item.get("proposal"),
        "review_start": place[0] if place else None, "review_end": place[1] if place else None,
    }


def _runs(records, loops):
    """The correction loop's round records for each finished loop, in order. A loop of n rounds wrote n records; a run
    that ended in a malformed reply or a failure refused the job and wrote no loop record, so it is skipped."""
    before = [record for record in records if record["stage"] == "before_human"]
    position, runs = 0, []
    for loop in loops:
        size = len(loop["rounds"])
        while True:
            window = before[position:position + size]
            bad = next((k for k, record in enumerate(window) if record["outcome"] not in {"flagged", "clear"}), None)
            if bad is None:
                break
            position += bad + 1
        runs.append(window)
        position += size
    return runs


def _loop_view(run, loop, state, exact, current_hash):
    current = loop["manifest_hash"] == current_hash
    rounds = []
    for number, item in enumerate(loop["rounds"]):
        record = run[number] if number < len(run) else None
        flags = record["flags"] if record is not None and record["outcome"] == "flagged" else ()
        # Only a finding of the final package's last round can lie in the review text as it is now.
        placeable = exact and current and item["outcome"] == "not_converged" and (
            record is not None and record.get("manifest_hash") == current_hash
        )
        rounds.append({
            "round": item["round"], "outcome": item["outcome"], "findings": item["findings"],
            "categories": dict(item["categories"]), "added": list(item["added"]),
            "proposals": list(item["proposals"]), "items": [_finding(flag, state, placeable) for flag in flags],
        })
    return {"converged": loop["converged"], "current": current, "rounds": rounds}


def tripwire_view(router, job_id, row, state):
    """The trip-wire's record of this job for the Operator: the correction loop's rounds (``before_human``) and the
    check after approval (``after_human``). Value-free except that the review text itself carries the package."""
    register = router.register
    if not hasattr(register, "tripwire_records"):
        return {"before_human": None, "after_human": None}
    records = register.tripwire_records(job_id)
    loops = register.tripwire_loops(job_id)
    current_hash = row["manifest_hash"]
    exact = state is not None and state.manifest_hash == current_hash
    before = None
    if loops:
        runs = list(zip(_runs(records, loops), loops))
        rendered = [_loop_view(run, loop, state, exact, current_hash) for run, loop in runs]
        index = next((i for i in range(len(runs) - 1, -1, -1) if runs[i][1]["manifest_hash"] == current_hash), -1)
        before = dict(rendered[index], earlier=[item for i, item in enumerate(rendered) if i != index % len(rendered)])
    after = None
    flagged = [record for record in records if record["stage"] == "after_human"]
    if flagged:
        record = flagged[-1]
        current = record.get("manifest_hash") == current_hash
        after = {
            "outcome": record["outcome"], "verdict": record["verdict"],
            "pending": tripwire_pending(router, job_id),
            "items": [_finding(item, state, exact and current) for item in record["flags"]],
        }
    return {"before_human": before, "after_human": after}


def tripwire_pending(router, job_id):
    """True while the check after approval holds this job for the Operator's re-approve or refuse."""
    register = router.register
    if not hasattr(register, "tripwire_state"):
        return False
    row = register.get_details(job_id)
    state = register.tripwire_state(job_id, stage="after_human")
    return (
        row["status"] == "Held-Offline" and state is not None and state["outcome"] == "flagged"
        and state["verdict"] is None
    )


def tripwire_verdict(router, job_id, verdict, *, actor):
    """The console's path to ``themis tripwire review``: ``reapprove`` sends exactly the approved package unchanged
    (no second check), ``refuse`` refuses the job. Only for a package the check after approval sent back."""
    from .router import GateFailure
    if verdict not in {"reapprove", "refuse"}:
        raise GateFailure("INVALID_REVIEW_ACTION", 400)
    if not tripwire_pending(router, job_id):
        raise GateFailure("TRIPWIRE_NOT_PENDING", 409)
    status = router.register.review_tripwire(
        job_id, "rejected" if verdict == "reapprove" else "confirmed", reviewer_identity=actor,
        reviewer_role="operator", now=router.clock(),
    )
    return {"job_id": job_id, "status": status.value}


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
    if whole is not None and segment not in router.vault.mapping(state.conversation_id):
        # FAB-03: a placeholder that is no longer in the codebook (an undo removed it) is never "confirmed as it is".
        raise GateFailure("REVIEW_REBUILD_REQUIRED", 409)
    if whole is not None and parse_strict_token(segment).token_class == token_class:
        reopen, added = False, (None, False, state.conversation_id)
    else:
        from .substitute import CATEGORY_STRENGTH
        # FAB-07: a weaker category over a stronger placeholder would lose to it when the package is rebuilt (the
        # strongest label wins a merged group) and the job would be refused on the next send; refuse it here instead.
        if any(CATEGORY_STRENGTH[parse_strict_token(match.group()).token_class] > CATEGORY_STRENGTH[token_class]
               for match in TOKEN.finditer(segment)):
            raise GateFailure("REPLACEMENT_CATEGORY_WEAKER", 400)
        original = _original(router, state, start, end)
        if len(original) > MAX_REPLACEMENT or not original.strip():
            raise GateFailure("INVALID_REPLACEMENT_SPAN", 400)
        created = original not in router.vault.mapping(state.conversation_id).values()
        try:
            token = TokenAllocator(state.conversation_id, router.vault).allocate(original, token_class)
        except Exception:
            # The same value already has a placeholder of another category in this conversation.
            raise GateFailure("INVALID_REPLACEMENT_SPAN", 400) from None
        if created:
            router.vault.mark_source(state.conversation_id, token, OPERATOR_SOURCE)
        with router._state_lock:
            router._operator_tokens.setdefault(job_id, set()).add(token)
        reopen, added = True, (token, created, state.conversation_id)
    router.register.resolve_point(
        job_id, point_id, "replaced", actor_identity=actor, manifest_hash=state.manifest_hash,
        category=category, start=start, end=end, reopen=reopen, now=router.clock(),
    )
    with router._state_lock:
        # In memory only, like the review text: what this replacement added, so that it can be undone.
        router._operator_points.setdefault(job_id, {})[point_id] = added
    return view(router, job_id)


def _in_use(router, job_id, conversation_id, token):
    """A placeholder already part of what was sent, or of another job's package, is never removed from the vault."""
    for turn in router.history.outbound(conversation_id):
        if token.encode("utf-8") in turn.payload.body:
            return True
    with router._state_lock:
        payloads = [raw for other, raw in router._payloads.items() if other != job_id]
    return any(token.encode("utf-8") in raw for raw in payloads)


def undo(router, job_id, point_id, *, actor):
    """Undo a mis-clicked "Not personal" or "Replace it" until stage two of the package is approved.

    The register keeps the settlement and records the undo (who, when); the point is open again. Undoing a
    replacement that added a placeholder removes it from the job's codebook (the conversation vault) and from the
    placeholders the rebuilt package must contain, and sends a ready package back to building, so the next send
    rebuilds it without that placeholder. A replacement made before the gate restarted cannot be undone (what it added
    was kept in memory only), nor one whose placeholder was already sent or is in another job's package."""
    from .router import GateFailure
    recorded = router.register.review_state(job_id)
    point = next(
        (item for item in recorded["points"] if item["id"] == point_id and item["settled_by"] == "operator"), None,
    )
    if point is None:
        raise KeyError("unknown point")
    resolution = point["resolution"]
    if resolution is None:
        raise GateFailure("POINT_NOT_SETTLED", 409)
    token, created, conversation = None, False, None
    if resolution["action"] == "replaced":
        with router._state_lock:
            entry = router._operator_points.get(job_id, {}).get(point_id)
        if entry is None:
            raise GateFailure("UNDO_UNAVAILABLE", 409)
        token, created, conversation = entry
        if created:
            with router._state_lock:
                shared = any(
                    other != point_id and item[0] == token
                    for other, item in router._operator_points.get(job_id, {}).items()
                )
            if shared:
                # FAB-07: another replacement of this job reuses the placeholder; removing it would make the next
                # send refuse the job (OPERATOR_REPLACEMENT_NOT_APPLIED). Undo that one first.
                raise GateFailure("PLACEHOLDER_SHARED", 409)
        if created and _in_use(router, job_id, conversation, token):
            raise GateFailure("PLACEHOLDER_IN_USE", 409)
    router.register.undo_point(job_id, point_id, actor_identity=actor, reopen=created, now=router.clock())
    with router._state_lock:
        points = router._operator_points.get(job_id, {})
        points.pop(point_id, None)
        if token is not None and not any(item[0] == token for item in points.values()):
            router._operator_tokens.get(job_id, set()).discard(token)
    if created:
        with router._state_lock:
            # FAB-03: the review text and points are those of the package that held the removed placeholder. Drop
            # them, so that no action is taken on stale text, until the requester re-sends and the package is rebuilt.
            router._reviews.pop(job_id, None)
            router.__dict__.setdefault("_review_stale", set()).add(job_id)
        try:
            router.vault.forget_operator_placeholder(conversation, token)
        except KeyError:
            pass  # already gone with its conversation
    return view(router, job_id)


def not_personal(router, job_id, point_id, *, actor):
    """"Not personal: leave it": the text stays as shown (a placeholder the gate already put in stays a placeholder;
    this never sends an original). Recorded and counted per hold reason; never promoted automatically. Refused while
    the review text is stale after an undo (FAB-03): the text the Operator would be confirming no longer exists."""
    _not_stale(router, job_id)
    router.register.resolve_point(job_id, point_id, "not-personal", actor_identity=actor, now=router.clock())
    return view(router, job_id)


def stop(router, job_id, point_id, *, actor):
    router.register.stop_document(job_id, point_id, actor_identity=actor, now=router.clock())
    return view(router, job_id)
