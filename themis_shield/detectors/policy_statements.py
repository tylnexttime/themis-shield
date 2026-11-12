"""v0.5 fix "policy-statements" (from split G's friction): a sensitive "statement" about nobody is not personal data.

Taxonomy section 3: "sensitive statements are labelled only when they are about a person", and section 4
(``policy_statement``): "staff with a disability can request reasonable adjustments" is not personal information. On
split G three clean documents were still held only because a finder (or the union-role pass) called such a sentence a
statement: "Staff with a disability can request reasonable adjustments ..." in a configuration comment and in a quoted
log notice, "The College does not ask whether any employee is or is not a union member ..." in an FAQ.

A sensitive finding, from any detector, is dropped when the clause it lies in is plainly a general rule about a class
of people and about no one in particular, by fixed wording only (``policy_clause``):

- a generic subject: a class of people in the plural (staff, employees, applicants, customers, ...), "any", "every",
  "each" or "no" with one of them in the singular, "anyone who", "those with", a trade-union topic ("union matters",
  "union activity"), or an organisation that "does not ask whether" any of them,
- with a modal or generic verb after it (only lower-case words between the two): "can request", "may apply",
  "are entitled to", "is treated as", "are governed by", "is a private matter", "does not ask whether",
- and nothing personal anywhere in the clause: no he/she/him/her/his/I/me/my/you/your, no capitalised word before the
  subject (a name), no "the only", "sole" or "one", no "a staff member at ..." or "the employee of ...", no speech verb
  ("asked", "said", "told": someone is reporting), and no PERSON or QUASI_ID finding in the clause.

A finding that spans several clauses of one sentence ("... at any stage of employment; requests are handled by the
People and Capability team.") is dropped only when one of them is such a rule and each of the others says nothing about
a sensitive category (no word of a short fixed list: disability, health, union, religion, criminal ...), holds no name
(a capitalised word before the rule, or a clause after it that starts with one) and is not personal. A finding across
sentences, a cell of a sensitive-column table and a finding that carries any other label are never dropped. Only the
finding goes: every other finding in the document is unchanged.

``policy_clause`` (one clause) and ``policy_statement`` (a span of a text) have no state, so any other pass can ask the
same question.
"""

from __future__ import annotations

import re

from ..placeholders import SENSITIVE_LABELS
from ..types import Label
from .statements import TABLE_STATEMENT_STAGE, _sentence_clause

_PLURAL = (r"(?:staff(?!\s+member\b)(?:\s+members)?|employees|workers|applicants|candidates|students|customers|clients"
           r"|patients|residents|volunteers|contractors|members|people|parents|carers|tenants|new\s+starters"
           r"|(?:team|crew)\s+members)")
_SINGULAR = (r"(?:staff(?:\s+member)?|employee|worker|team\s+member|applicant|candidate|student|customer|client|patient"
             r"|resident|volunteer|contractor|member|person)")
_ORGANISATION = (r"(?:college|organisation|organization|company|employer|school|university|council|firm|authority"
                 r"|department|agency|we)")
_SUBJECTS = (
    re.compile(r"(?i)\b" + _PLURAL + r"\b"),
    re.compile(r"(?i)\b(?:any|every|each|no)\s+(?:(?:new|current|permanent|casual|eligible)\s+)?" + _SINGULAR + r"\b"),
    re.compile(r"(?i)\b(?:anyone|anybody|everyone|those|no\s+one)\s+(?:who|with)\b"),
    re.compile(r"(?i)\b(?:trade\s+)?union\s+(?:matters|activity|activities|membership|delegates|representatives|reps)\b"),
)
# "The College does not ask whether any employee ...": the organisation is the subject, the class is what it never asks.
_ASKS = re.compile(
    r"(?i)\b(?:the\s+)?" + _ORGANISATION + r"\s+(?:does|do)\s+not\s+(?:ask|require|record|collect|keep)\s+(?:whether|if)\s+"
    r"(?:(?:any|every|each)\s+" + _SINGULAR + r"|" + _PLURAL + r")\b")
# Lower case only: a "May" is a month, never the verb.
_VERB = re.compile(
    r"\b(?:(?:can|could|may|might|must|should|shall|will)(?:\s+(?:also|always|only|not|never))?\b|cannot\b"
    r"|(?:is|are)\s+(?:(?:not|never|always)\s+)?(?:entitled|eligible|required|expected|able|allowed|permitted|welcome|free"
    r"|encouraged)\b"
    r"|(?:is|are)\s+(?:(?:not|never|always)\s+)?(?:treated|governed|handled|regarded|considered|kept|held|recorded|covered"
    r"|protected|managed|assessed)\b"
    r"|(?:is|are)\s+(?:a\s+)?(?:private|personal|confidential)\s+matter\b"
    r"|(?:does|do)\s+not\s+ask\s+(?:whether|if)\b)")
_GAP = re.compile(r"(?:\s+[a-z][a-z'\u2019-]*){0,8}\s+")  # lower-case words only between the subject and its verb

_PRONOUN = re.compile(r"(?i)\b(?:he|she|him|her|hers|his|himself|herself|i|me|my|mine|myself|you|your|yours|yourself)\b")
_ROLE = (r"(?:staff\s+member|member\s+of\s+staff|employee|worker|colleague|team\s+member|contractor|volunteer|student"
         r"|customer|client|patient|resident|applicant|candidate|person|individual|manager|supervisor|teacher|nurse"
         r"|officer|man|woman)")
_SPECIFIC = re.compile(
    r"(?i)\bthe\s+only\b|\bsole\b|(?<!no )(?<!any )(?<!every )(?<!each )\bone\b"
    r"|\b(?:a|an|the|this|that|our)\s+(?:[\w'\u2019-]+\s+){0,2}?" + _ROLE + r"\s+(?:at|on|in|from|of|for|named|called)\b")
_REPORTING = re.compile(
    r"(?i)\b(?:asked|asks|asking|said|says|told|wondered|wanted|mentioned|confirmed|explained|complained|claimed|emailed"
    r"|wrote|phoned)\b")
# A clause beside the rule that is not itself a rule must say nothing about a sensitive category: a finder's span often
# runs on into the next clause ("...; the night analyst has epilepsy"). Word stems, matched at the start of a word.
_SENSITIVE_WORDS = re.compile(
    r"(?i)\b(?:disab|impair|wheelchair|deaf|blind|hearing|vision|medic|health|ill\b|illness|sick|diagnos|condition|epilep"
    r"|diabet|cancer|depress|anxi|mental|bipolar|pregnan|injur|surger|hospital|therap|disorder|syndrome|chronic|union"
    r"|steward|delegate|religio|faith|church|mosque|temple|synagogue|pray|halal|kosher|christian|muslim|jewish|hindu"
    r"|buddhis|sikh|criminal|convict|offen[cs]e|charged|prison|jail|parole|bail|police|court|custod|gay|lesbian"
    r"|bisexual|transgender|sexual|partner|husband|wife|girlfriend|boyfriend|indigenous|aboriginal|torres|ethnic|racial"
    r"|race|violence|abuse|refuge|protection\s+order)")
_WORD = re.compile(r"[^\W\d_][\w'\u2019-]*")
_CONNECTOR = re.compile(r"(?i)^\W*(?:(?:and|or|but|so|then|while|whereas|because)\s+)?([^\W\d_])")
_CLAUSE_PIECE = re.compile(r"[^:;,\n]+")
_SENTENCE_INSIDE = re.compile(r"[.!?][\"'\u201d\u2019)]*\s|\n\s*\n")
_REACH = 1500  # a sentence that reaches further than this from the span is not read as a rule (and is cheap to skip)
_TRAILING = " \t.!?\"'\u201d\u2019)"
_QUOTES = " \t\"'\u201c\u2018"


def _names(prefix: str, initial: bool) -> bool:
    """True when the words before the subject include a capitalised one: a name. The first word of a clause that
    starts a sentence (or follows a colon) is capitalised anyway; after a comma or semicolon it is not."""
    words = _WORD.findall(prefix)
    if initial and words and words[0].endswith(("'s", "’s")):
        return True  # "Dana's staff ...": the sentence start is a person's
    return any(word[0].isupper() for word in (words[1:] if initial else words))


def _rule(clause: str, initial: bool = True) -> bool:
    """A generic subject followed by a modal or generic verb, with only lower-case words between them and no name
    before the subject."""
    found = _ASKS.search(clause)
    if found is not None and not _names(clause[:found.start()], initial):
        return True
    for subject in _SUBJECTS:
        for match in subject.finditer(clause):
            verb = _VERB.search(clause, match.end())
            if verb is None or _GAP.fullmatch(clause, match.end(), verb.start()) is None:
                continue
            if not _names(clause[:match.start()], initial):
                return True
    return False


def _personal(clause: str) -> bool:
    return bool(_PRONOUN.search(clause) or _SPECIFIC.search(clause) or _REPORTING.search(clause))


def policy_clause(clause: str) -> bool:
    """One clause is plainly a general rule about a class of people: a generic subject, a modal or generic verb, and
    nothing personal in it (see the module docstring)."""
    return _rule(clause) and not _personal(clause)


def _initial(text: str, at: int) -> bool:
    """False when the clause starting at ``at`` follows a comma or a semicolon (its first word is not a sentence start)."""
    while at > 0 and text[at - 1] in _QUOTES:
        at -= 1
    return not (at > 0 and text[at - 1] in ";,")


def _pieces(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The clauses of the sentence that the span ``[start, end)`` touches, as spans (none when the sentence is longer
    than ``_REACH`` characters either side of the span)."""
    lo, hi = max(0, start - _REACH), min(len(text), end + _REACH)
    left, right = _sentence_clause(text[lo:hi], start - lo, end - lo)
    if (lo and left == 0) or (hi < len(text) and right == hi - lo):
        return []
    left, right = left + lo, right + lo
    return [
        (left + m.start(), left + m.end()) for m in _CLAUSE_PIECE.finditer(text[left:right])
        if text[left + m.start():left + m.end()].strip() and left + m.start() < end and start < left + m.end()
    ]


def policy_statement(text: str, start: int, end: int, people: tuple[tuple[int, int], ...] = ()) -> bool:
    """True when the span ``[start, end)`` lies in one sentence and the clauses it touches are, as a group, a general
    rule: one of them is a rule (see ``policy_clause``), none is personal, each of the others says nothing sensitive and
    holds no name, and no span of ``people`` (PERSON and QUASI_ID findings) overlaps any of them."""
    while start < end and text[start].isspace():
        start += 1
    # A span that ends with the full stop (or the closing quote after it) must not reach into the next sentence.
    end = start + len(text[start:end].rstrip(_TRAILING))
    if start >= end or _SENTENCE_INSIDE.search(text[start:end]):
        return False
    pieces = _pieces(text, start, end)
    if not pieces:
        return False
    ruled = False
    for a, b in pieces:
        clause, initial = text[a:b], _initial(text, a)
        if _personal(clause) or any(p < b and a < q for p, q in people):
            return False
        if _rule(clause, initial):
            ruled = True
        elif _SENSITIVE_WORDS.search(clause):
            return False  # another clause that speaks of a sensitive category and is not a rule itself
        elif not ruled and _names(clause, initial):
            return False  # a lead-in before the rule that holds a name or a place ("8.2 Tobias Varga, who is ...")
        elif ruled and (m := _CONNECTOR.match(clause)) and m.group(1).isupper():
            return False  # a clause after the rule that starts with a name ("; Dana's request was approved")
    return ruled


def drop_policy_statements(text: str, findings):
    """The findings without the sensitive ones that lie in a general rule (``policy_statement``)."""
    people = tuple((item.start, item.end) for item in findings if item.labels & {Label.PERSON, Label.QUASI_ID})
    kept = []
    for item in findings:
        if (item.labels and item.labels <= SENSITIVE_LABELS
                and not any(entry.stage == TABLE_STATEMENT_STAGE for entry in item.evidence)):
            span = item.statement_span
            start, end = (item.start, item.end) if span is None else (min(item.start, span.start), max(item.end, span.end))
            if policy_statement(text, start, end, people):
                continue
        kept.append(item)
    return tuple(kept)