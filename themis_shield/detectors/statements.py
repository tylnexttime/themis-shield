"""Deterministic sensitive-statement passes (v0.4 item 4), run with propagation in tiers 1 and final.

After 0.3.2 every sensitive statement the gate still missed on the development sets sat in a held document, and most
were of two kinds: table cells under a column that names a sensitive category (a police check result "Clear", a union
status "non-member"), and a person's trade-union role in prose ("is the shop steward on B shift", "collects the dues
envelope from the crew"). Both are found here by fixed vocabulary, never by anything learned from a data set.

- ``sensitive-columns``: a table column whose header names a sensitive category marks every non-empty cell in it as a
  statement of that category. A "No", "Clear" or "nil" still says something about the person, as the finder prompt
  already instructs ("a negative statement or a 'No' answer ... is still personal information").
- ``union-roles``: a sentence that gives a person a trade-union role or act (steward, delegate, rep or member; paying
  or collecting dues; joining, leaving or recruiting for the union; the picket line) is a UNION statement. Generic
  policy ("Union delegates may attend ..."), credit unions, student unions and the European Union are not: the
  patterns need a singular subject verb or a personal act, never a plural subject with a modal.

Each finding's statement span is the cell or the sentence, so it is substituted whole.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from functools import lru_cache
import re

from ..types import Label, Span
from .contracts import DetectionFinding, finding

TABLE_STATEMENT_STAGE = "table-statements"
UNION_ROLE_STAGE = "union-roles"
_SEPS = (",", "\t", ";", "|")
_ALIGN_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
MAX_CELL = 160

# Header vocabulary -> category. Matched on the whole normalised header (lower case, "_" and "-" as spaces).
_SENSITIVE_COLUMNS = (
    (Label.CRIMINAL, re.compile(
        r"(?:national\s)?police\s(?:check|clearance|record|history)(?:\s(?:result|outcome|status))?"
        r"|criminal\s(?:history|record|check|convictions?)(?:\s(?:result|outcome|status|check))?"
        r"|convictions?|disclosures?(?:\son\sfile)?|disclosable\soutcomes?|npc(?:\s(?:result|outcome|status))?")),
    (Label.UNION, re.compile(
        r"union|union\s(?:member|membership|status|delegate|rep)|trade\sunion(?:\smembership)?|membership\s\(union\)")),
    (Label.HEALTH, re.compile(
        r"(?:medical|health)(?:\s(?:condition|conditions|notes?|history|issues?|status))?|diagnosis|diagnoses"
        r"|medications?|allergies|allergy")),
    (Label.DISABILITY, re.compile(r"disability|disabilities|disability\s(?:status|type|support)|impairment")),
    (Label.RELIGION, re.compile(r"religion|religious\saffiliation|faith|denomination")),
    (Label.SEXUALITY, re.compile(r"sexuality|sexual\sorientation")),
    (Label.ETHNICITY, re.compile(
        r"ethnicity|ethnic\s(?:background|origin|group)|cultural\sbackground"
        r"|(?:aboriginal|indigenous)(?:\sor\storres\sstrait\sislander)?(?:\sstatus)?|atsi(?:\sstatus)?")),
)


def _column_label(name: str) -> Label | None:
    key = " ".join(name.casefold().replace("_", " ").replace("-", " ").split())
    for label, pattern in _SENSITIVE_COLUMNS:
        if pattern.fullmatch(key):
            return label
    return None


def _cells(line: str, sep: str) -> list[tuple[int, int]]:
    from .propagate import _cells as cells
    return cells(line, sep)


def _statement(start: int, end: int, label: Label, stage: str, tier: int) -> DetectionFinding:
    item = finding(start, end, label, stage, tier)
    return replace(item, statement_span=Span(start, end))


def table_statement_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    out, index = [], 0
    while index < len(lines):
        header = lines[index].rstrip("\r\n").lstrip("﻿")
        separator = max(_SEPS, key=header.count)
        if header.count(separator) < (2 if separator == "|" else 1) or any(ch.isdigit() for ch in header):
            index += 1
            continue
        try:
            cells = _cells(header, separator)
        except (csv.Error, ValueError, StopIteration):
            index += 1
            continue
        columns = {j: label for j, (a, b) in enumerate(cells) if (label := _column_label(header[a:b])) is not None}
        if not columns:
            index += 1
            continue
        row_index = index + 1
        while row_index < len(lines):
            row = lines[row_index].rstrip("\r\n")
            if separator == "|" and _ALIGN_ROW.fullmatch(row):
                row_index += 1
                continue
            if not row.strip() or separator not in row:
                break
            try:
                row_cells = _cells(row, separator)
            except (csv.Error, ValueError, StopIteration):
                break
            extra = len(row_cells) - len(cells)
            if extra < 0:
                break
            for column, label in columns.items():
                # As for identifier columns: an unquoted comma earlier in the row shifts this cell; take both.
                for at in sorted({column, column + extra}):
                    a, b = row_cells[at]
                    value = row[a:b]
                    if value.strip(" -–—") and len(value) <= MAX_CELL and any(ch.isalnum() for ch in value):
                        out.append(_statement(offsets[row_index] + a, offsets[row_index] + b, label,
                                              TABLE_STATEMENT_STAGE, tier))
            row_index += 1
        index = row_index if row_index > index + 1 else index + 1
    return tuple(out)


# A person's union role or act. Every pattern needs a singular or personal verb form; plural subjects with "may",
# "can" or "are covered" (policy) never match, and "credit/student union", "European Union" are excluded below.
# Only roles that are unambiguously trade-union: "shop/union steward" (never a bare steward: a data steward is a
# governance role), "union delegate/rep/official/organiser", and a delegate qualified as workplace, site, depot or
# floor, elected, or "for/by the <crew, workers, staff, shift, members, branch>" (never a bare delegate: a conference
# has delegates too).
_DELEGATE = (r"(?:(?:workplace|site|depot|floor|shop|elected)\s+delegate"
             r"|delegate(?=\s+(?:for|of|by)\s+the\s+[\w\s'-]{0,30}?\b(?:crew|workers|staff|shift|members|union|branch)\b))")
_ROLE = (r"(?:(?:shop|union)\s+steward|union\s+(?:delegate|rep|representative|official|organiser|organizer|member)"
         r"|" + _DELEGATE + r")")
_UNION_ROLES = re.compile(
    r"(?ix)"
    r"\b(?:is|was|am|i'm|has\s+been|have\s+been|became|been\s+elected|was\s+elected|elected(?:\s+as)?)"
    r"(?:\s+(?:the|a|an|our|their|his|her|my))?(?:\s+[\w'-]+){0,3}?\s+" + _ROLE + r"\b"
    r"|\b(?:was|been|is|got)\s+elected\s+(?:as\s+)?(?:the\s+|a\s+)?delegate\s+(?:by|for)\s+the\b"
    r"|\b(?:pays|paid|paying|collects?|collected|collecting)\s+(?:[\w'-]+\s+){0,3}?(?:union\s+)?(?:dues|union\s+fees)\b"
    r"|\b(?:joined|left|quit|resigned\s+from|signed\s+up\s+(?:with|to)|(?:is|was|am)\s+(?:not\s+)?(?:a\s+)?member\s+of"
    r"|(?:is|am|was)\s+(?:not\s+)?in)\s+the\s+(?:[\w'-]+\s+){0,2}?union\b"
    r"|\b(?:recruited|signed\s+up)\s+[\w\s]{0,30}?(?:into|to)\s+the\s+union\b"
    r"|\b(?:is|was|am|isn't|wasn't)\s+(?:not\s+)?(?:a\s+)?(?:long-time\s+)?union\s+member\b"
    r"|\b(?:stood|was|were|been|stands|joined)\s+on\s+the\s+picket\s+line\b"
    r"|\b(?:wore|wears|handed\s+out|hands\s+out|organises|organizes|chairs)\s+(?:the\s+)?(?:local\s+)?union\b"
    r"|\b(?:lodged|raised|filed|took)\s+(?:[\w'-]+\s+){0,4}?through\s+(?:the|her|his|their|my)\s+union\b"
    r"|\b(?:had|got)\s+(?:her|his|their|my)\s+union\b"
)
_NOT_TRADE_UNION = re.compile(r"(?i)\b(?:credit|student|students'|european|customs|rugby|union\s+(?:street|st|road|rd|"
                              r"square|station|hotel|bank|pacific|jack))\b")
_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n")
_CLAUSE_BREAK = re.compile(r"[:;,]")


def union_role_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    out = []
    for match in _UNION_ROLES.finditer(text):
        ends = [m.end() for m in _SENTENCE_END.finditer(text, 0, match.start())]
        start = ends[-1] if ends else 0
        after = _SENTENCE_END.search(text, match.end())
        end = after.start() + (1 if after and after.group() != "\n" else 0) if after else len(text)
        while start < end and text[start].isspace():
            start += 1
        sentence = text[start:end]
        if _NOT_TRADE_UNION.search(sentence) and "union" in match.group().casefold():
            continue
        # The statement is the clause, not the whole sentence: in a log line the "sentence" also holds the time, a
        # ticket number and a site ("06:05 MNT-26-96173 welfare flag: Sunita ... is the shop steward on B shift").
        # Bounded by the last ":", ";" or "," before the match and the first ";" or "," after it.
        clause = [m.end() for m in _CLAUSE_BREAK.finditer(text, start, match.start())]
        if clause:
            start = clause[-1]
        later = _CLAUSE_BREAK.search(text, match.end(), end)
        if later is not None and later.group() != ":":
            end = later.start()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1] in " \t.!?":
            end -= 1
        if start < end:
            out.append(_statement(start, end, Label.UNION, UNION_ROLE_STAGE, tier))
    return tuple(out)


# v0.5 fix "negative-statements" (from split F): a statement that a person does NOT have a sensitive attribute still
# says something about them ("is not on any medication", "denies any thoughts of self-harm", "the police certificate
# shows no matters", "is not experiencing family violence", "am not a member of any union", a "No" after a question
# about Aboriginal or Torres Strait Islander origin). The finders find the positive statements and miss most negative
# ones. Fixed vocabulary only: a negation (no, not, never, nil, denies) next to a category's own words; nothing learned
# from any data. The words "matters" and "outcomes" are criminal only near a police, court, conviction or criminal
# word, and a form answer counts only when it is the whole answer (never "Yes / No" options on a blank form).
NEGATIVE_STATEMENT_STAGE = "negative-statements"
_NEGATIVE_PATTERNS = (
    (Label.CRIMINAL, re.compile(
        r"(?ix)\b(?:no|nil|not\s+any)\s+(?:(?:new|further|other|disclosable|recorded|relevant|prior|previous)\s+)*"
        r"(?:court\s+outcomes?|(?:criminal\s+)?convictions?|criminal\s+(?:history|records?|matters|charges|offences)"
        r"|police\s+record|offences)\b"
        r"|\b(?:has|have|had)\s+never\s+been\s+(?:charged|convicted|arrested|jailed|imprisoned|in\s+prison)\b"
        r"|\b(?:has|have|had)\s+(?:no|never\s+had\s+a(?:ny)?)\s+criminal\s+(?:record|history|convictions?)\b")),
    (Label.HEALTH, re.compile(
        r"(?ix)\b(?:is|am|was|are|were)\s+not\s+(?:on|taking)\s+any\s+(?:(?:regular|prescribed|other)\s+)?"
        r"(?:medications?|medicines?)\b"
        r"|(?:^|(?<=[.:;]\s)|(?<=\n))[ \t]*(?:no|nil)\s+(?:regular|current|known|prescribed)\s+(?:medications?|medicines?)\b"
        r"|\bdenies\s+(?:any\s+)?(?:(?:current|recent|thoughts\s+of|history\s+of|suicidal)\s+)*"
        r"(?:self-harm|self\s+harm|suicidal\s+(?:thoughts|ideation)|suicid(?:e|ality)|thoughts\s+of\s+harm|alcohol\s+use"
        r"|drug\s+use|substance\s+use|hallucinations)\b")),
    (Label.FAMILY_VIOLENCE, re.compile(
        r"(?ix)\b(?:is|am|was|has|have|had)\s+(?:not|never)\s+(?:been\s+)?(?:experiencing|experienced"
        r"|subjected\s+to|affected\s+by|a\s+victim\s+of)\s+(?:any\s+)?(?:family|domestic|family\s+(?:and|or)\s+domestic)"
        r"\s+violence\b")),
    (Label.UNION, re.compile(
        r"(?ix)\b(?:is|am|was|i'm)\s+(?:not|no\s+longer)\s+(?:a\s+)?member\s+of\s+(?:any|a)\s+(?:trade\s+)?union\b")),
)
# From split G (development data): a police check a person was never the subject of, and a file with no family
# violence indicators, flags or history on it.
_NEGATIVE_RECORDS = (
    (Label.CRIMINAL, re.compile(
        r"(?ix)\b(?:has|have|had)\s+never\s+been\s+(?:the\s+)?subject\s+(?:of|to)\s+(?:a|any)\s+police\s+checks?\b")),
    (Label.FAMILY_VIOLENCE, re.compile(
        r"(?ix)\b(?:has|have|had)\s+no\s+(?:(?:known|recorded|current)\s+)?(?:family|domestic|family\s+(?:and|or)\s+domestic)"
        r"\s+violence\s+(?:indicators?|flags?|history)\b")),
)
# "does not have any (other) court orders": a family violence statement when its paragraph speaks of a protection or
# intervention order or of family violence, otherwise a criminal one; never in a paragraph about a company.
_COURT_ORDERS = re.compile(
    r"(?ix)\b(?:does\s+not|doesn't|do\s+not|don't|did\s+not|didn't)\s+(?:currently\s+)?have\s+any\s+"
    r"(?:(?:other|further|current)\s+)?court\s+orders?\b"
    r"|\b(?:has|have)\s+no\s+(?:(?:other|further|current)\s+)?court\s+orders?\b")
_FAMILY_VIOLENCE_CONTEXT = re.compile(
    r"(?i)\b(?:intervention|protection|apprehended\s+violence|domestic\s+violence)\s+orders?\b|\bavo\b|\bdvo\b"
    r"|\b(?:family|domestic)\s+violence\b")
_MATTERS = re.compile(
    r"(?ix)\bno\s+(?:(?:new|further|other|disclosable)\s+)*matters\s+(?:declared|disclosed|recorded|listed|on\s+file)\b"
    r"|\b(?:check|certificate|clearance)\b[\w '-]{0,40}?\b(?:shows?|showed|returned|returns|came\s+back\s+with|lists?"
    r"|listed|records?|recorded|discloses|disclosed)\s+no\s+(?:(?:new|further|other|disclosable)\s+)*"
    r"(?:matters|outcomes)\b")
_CRIMINAL_CONTEXT = re.compile(r"(?i)\b(?:police|criminal|convictions?|court|offences?|corrections)\b")
CRIMINAL_CONTEXT_CHARS = 400
# A court report about a company ("the company had no prior convictions", "No conviction was recorded" after a
# sentence about the company) is not a person's record: no criminal statement when its paragraph (line) names a
# company.
_COMPANY = re.compile(r"(?i)\b(?:company|company's|companies|business|firm|corporation|pty|ltd)\b")
_STATEMENT_END = re.compile(r"[.!?][\"'\u201d\u2019)]*(?=\s)|\n")
# A question naming a category, then its whole answer on the same line ("... Torres Strait Islander origin? No").
_QUESTION_CATEGORIES = (
    (Label.ETHNICITY, re.compile(r"(?i)\b(?:aboriginal|torres\s+strait|indigenous|ethnicity|ethnic\s+(?:background|origin)"
                                 r"|cultural\s+background)\b")),
    (Label.CRIMINAL, re.compile(r"(?i)\b(?:criminal|convictions?|convicted|police\s+(?:check|record)|court\s+outcomes?)\b")),
    (Label.UNION, re.compile(r"(?i)\b(?:union\s+member(?:ship)?|member\s+of\s+(?:a|any|the)\s+(?:trade\s+)?union)\b")),
    (Label.DISABILITY, re.compile(r"(?i)\bdisabilit(?:y|ies)\b")),
)
_ANSWERED_QUESTION = re.compile(
    r"(?im)^[^\n?]{0,200}\?[ \t]*(yes|no|nil|none|unknown|declined|not\s+stated|prefer\s+not\s+to\s+say)[ \t]*[.;]?[ \t]*$")
# The same written as a form line that ends with ":" ("4.4 I do not identify as Aboriginal or Torres Strait Islander:
# No"): only a whole "Yes" or "No", and only when the line speaks of a person ("I", "you", "the applicant"), so a
# requirement line ("Police check required: Yes") or a heading is not one.
_ANSWERED_FORM_LINE = re.compile(r"(?im)^([^\n?:]{0,200}):[ \t]*(yes|no)[ \t]*[.;]?[ \t]*$")
_FORM_PERSON = re.compile(
    r"(?i)\b(?:i|you|he|she|my|your|his|her|applicant|client|patient|employee|worker|candidate)\b")
_FORM_REQUIREMENT = re.compile(
    r"(?i)\b(?:required|requirement|needed|mandatory|essential|necessary|must|should|may|will|shall)\b")


def _sentence_clause(text: str, start: int, end: int) -> tuple[int, int]:
    """The clause around a match: its sentence, cut at the last ":", ";" or "," before it and the first ";" or ","
    after it (as for union roles), without the surrounding spaces and the final stop."""
    ends = [m.end() for m in _STATEMENT_END.finditer(text, 0, start)]
    left = ends[-1] if ends else 0
    after = _STATEMENT_END.search(text, end)
    right = after.start() if after else len(text)
    clause = [m.end() for m in _CLAUSE_BREAK.finditer(text, left, start)]
    if clause:
        left = clause[-1]
    later = _CLAUSE_BREAK.search(text, end, right)
    if later is not None and later.group() != ":":
        right = later.start()
    while left < right and (text[left].isspace() or text[left] in "\"'“‘"):
        left += 1
    while right > left and text[right - 1] in " \t.!?":
        right -= 1
    return left, right


# DD-26 (from split G's GT-008, development data): a negative inside a general rule about a class of people ("A person
# with no criminal record at all is treated the same as any other applicant once the check is returned as clear") says
# nothing about anyone: taxonomy section 3 labels a statement only when it is about a person, section 4 calls this a
# policy_statement. By fixed wording: only a generic subject (a person, any applicant, anyone, those, or a plural such
# as applicants, staff or people only when marked as a class: see FAB-02 below), stands before the negative
# in its sentence, after at most a numbering and some lead-in words ("if", "what happens if", "and"); nothing personal
# is in the sentence (``policy_statements._personal``: a pronoun, "the only", a speech verb ...) and no ":" follows the
# negative in its clause (a list under a generic heading is about the people it lists). A name, "He", "I", an
# appositive after a comma ("Priya Nand, a person with no criminal record, applied") or any other word before the
# subject keeps the statement. Form answers ("... convictions? No") are another path and are not touched.
_GENERIC_LEAD_IN = (
    r"(?:(?:if|when|where|once|whether|because|although|while|and|but|so|then|also|does|do|will|would|can|could|should"
    r"|is|are|what\s+happens\s+if|note\s+that|generally|usually|normally|typically|in\s+most\s+cases|in\s+practice"
    r"|for\s+example)\s*,?\s+)*")


@lru_cache(maxsize=1)
def _generic_subject() -> re.Pattern:
    from .policy_statements import _PLURAL, _SINGULAR  # not at the top: that module imports this one

    return re.compile(
        r"(?i)[\s\"'“‘(]*(?:\d+(?:\.\d+)*\.?\s+)?" + _GENERIC_LEAD_IN
        + r"(?:(?P<marked>a\s+person|(?:any|every|each)\s+(?:(?:new|current|permanent|casual|eligible)\s+)?" + _SINGULAR
        + r"|anyone|anybody|everyone|those)|(?:(?P<quantified>all|any)\s+)?(?P<bare>" + _PLURAL + r"))"
        + r"(?:\s+(?P<relative>with|who|that))?(?:\s+(?:currently|already|still|also|ever|really))*"
        + r"(?:\s+(?:has|have|had))?\s*$")


# FAB-02 (independent review): a bare plural subject ("Parents have no criminal record.", "Carers have never been
# charged.") is two or more identifiable people in a case note, not a rule. A plural subject is generic only with an
# explicit marker: "with", "who" or "that" after it, and then either a quantifier before it ("all", "any") or a modal
# rule after the negative in its clause ("may", "can", "are not required", "need not"). In doubt the statement stays.
_RULE_AFTER = re.compile(r"\b(?:need\s+not|needn[’']t|(?:does|do)\s+not\s+need)\b")


def _generic_subject_statement(text: str, a: int, b: int, end: int) -> bool:
    """True when the negative at ``[a, b)``, in the clause that ends at ``end``, is said of a generic subject (see
    above)."""
    ends = [m.end() for m in _STATEMENT_END.finditer(text, 0, a)]
    sentence = ends[-1] if ends else 0
    subject = _generic_subject().match(text, sentence, a)
    if subject is None or ":" in text[b:end]:
        return False
    if subject.group("bare") is not None:
        from .policy_statements import _VERB

        if subject.group("relative") is None:
            return False
        if subject.group("quantified") is None and not (_VERB.search(text, b, end) or _RULE_AFTER.search(text, b, end)):
            return False
    from .policy_statements import _personal

    return not _personal(text[sentence:end])


def negative_statement_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    out = []
    found = [(label, m.start(), m.end()) for label, pattern in _NEGATIVE_PATTERNS + _NEGATIVE_RECORDS
             for m in pattern.finditer(text)]
    found += [
        (Label.CRIMINAL, m.start(), m.end()) for m in _MATTERS.finditer(text)
        if _CRIMINAL_CONTEXT.search(text, max(0, m.start() - CRIMINAL_CONTEXT_CHARS), m.end())
    ]
    found += [
        (Label.FAMILY_VIOLENCE if _FAMILY_VIOLENCE_CONTEXT.search(
            text, max(0, m.start() - CRIMINAL_CONTEXT_CHARS), m.end()) else Label.CRIMINAL, m.start(), m.end())
        for m in _COURT_ORDERS.finditer(text)
    ]
    for label, a, b in found:
        while a < b and text[a].isspace():
            a += 1
        if label is Label.CRIMINAL or label is Label.FAMILY_VIOLENCE and _COURT_ORDERS.match(text, a, b):
            line_end = text.find("\n", b)
            if _COMPANY.search(text, text.rfind("\n", 0, a) + 1, len(text) if line_end < 0 else line_end):
                continue
        start, end = _sentence_clause(text, a, b)
        if start < end and not _generic_subject_statement(text, a, b, end):
            out.append(_statement(start, end, label, NEGATIVE_STATEMENT_STAGE, tier))
    for m in _ANSWERED_QUESTION.finditer(text):
        question = text[m.start():m.start(1)]
        for label, pattern in _QUESTION_CATEGORIES:
            if pattern.search(question):
                out.append(_statement(m.start(1), m.end(1), label, NEGATIVE_STATEMENT_STAGE, tier))
                break
    for m in _ANSWERED_FORM_LINE.finditer(text):
        question = m.group(1)
        if not _FORM_PERSON.search(question) or _FORM_REQUIREMENT.search(question):
            continue
        for label, pattern in _QUESTION_CATEGORIES:
            if pattern.search(question):
                out.append(_statement(m.start(2), m.end(2), label, NEGATIVE_STATEMENT_STAGE, tier))
                break
    return tuple(out)


# v0.5 fix "indirect-statements" (from split G, development data): a sensitive statement that does not name its
# category, in the taxonomy's own examples: "did two years inside", "is on a community corrections order", "is on
# parole" (CRIMINAL); "leaves early on Fridays for Shabbat", "keeps kosher" (RELIGION); "carries the delegate's card",
# "I am the delegate contact for outdoor staff" (UNION). Fixed vocabulary only. Each pattern needs a person: a
# singular or personal verb form in it, or (where the words alone could be anyone's or a kitchen's: "did time inside",
# "for Shabbat", "keeps kosher") a personal pronoun in its clause, up to the end of the match ("he", "my", "his halal
# meal"); a modal, "if" or "any applicant" before it (policy: "staff on a community corrections order may apply",
# "the canteen offers halal options") never matches, and neither does a
# document that presents itself as fiction (the taxonomy's fiction_statement: "Short story: ...", "CHAPTER SEVEN").
# The span is the predicate phrase (the taxonomy's span rule): from the match to the end of its clause, cut before a
# following "and", "but" or "because", so a role title, site or committee around it stays out.
INDIRECT_STATEMENT_STAGE = "indirect-statements"
_PRONOUN = re.compile(r"(?i)\b(?:i|i'm|i've|he|she|he's|she's|him|his|her|me|my|himself|herself|myself)\b")
_TIME = (r"(?:time|a\s+(?:long\s+|short\s+)?stretch"
         r"|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|eighteen|twenty|a\s+few"
         r"|several|\d{1,2})\s+(?:and\s+a\s+half\s+)?(?:months?|years?|weeks?)(?:\s+and\s+a\s+half)?)")
_INDIRECT_PATTERNS = (
    # (label, pattern, needs a personal pronoun in its clause, up to the end of the match)
    (Label.CRIMINAL, re.compile(
        r"(?ix)\b(?:did|does|doing|done|served|serving|serves)\s+" + _TIME + r"\s+inside\b"
        r"(?!\s+(?:the|a|an|his|her|its|our|their|this|that)\b)"
        r"|\b(?:spent|spending|spends)\s+(?!time\b)" + _TIME + r"\s+inside\b"
        r"(?!\s+(?:the|a|an|his|her|its|our|their|this|that)\b)"), True),
    (Label.CRIMINAL, re.compile(
        r"(?ix)\b(?:is|was|am|i'm|he's|she's|been)\s+(?:(?:currently|still|now|already)\s+)?on\s+a\s+"
        r"community\s+corrections\s+order\b"
        r"|\b(?:completed|finished|finishes|finishing|serving|serves|served)\s+(?:a|an|his|her|my)\s+"
        r"(?:[\w-]+\s+)?community\s+corrections\s+order\b"
        r"|\b(?:is|was|am|i'm|he's|she's|been)\s+(?:(?:currently|still|now|out)\s+)*on\s+(?:parole|bail)\b"
        r"|\b(?:was|been|got)\s+(?:granted|refused|released\s+on)\s+(?:parole|bail)\b"
        r"|\b(?:sees|seeing|saw|reports\s+to|reporting\s+to|reported\s+to|meets\s+with|met\s+with)\s+(?:his|her|my)\s+"
        r"parole\s+officer\b"
        r"|\b(?:breached|breaching|breaches)\s+(?:his|her|my)\s+(?:parole|bail)\b"), False),
    (Label.RELIGION, re.compile(
        r"(?ix)\b(?:leaves|left|finishes|finished|goes\s+home|went\s+home|heads\s+home|is\s+off|takes\s+off)\s+"
        r"(?:[\w':]+\s+){0,5}?for\s+(?:the\s+)?(?:shabbat|shabbos|sabbath)\b"
        r"|\b(?:observes|observed|keeps|kept)\s+(?:the\s+)?(?:shabbat|shabbos|sabbath)\b"
        r"|\b(?:i\s+(?:only\s+)?(?:eat|need|keep)|eats|needs|requires|keeps|kept)\s+(?:(?:only|strictly)\s+)?"
        r"(?:halal|kosher)\b"
        r"|\b(?:his|her|my)\s+(?:halal|kosher)\s+(?:meals?|diet|food|options?)\b"), True),
    (Label.UNION, re.compile(
        r"(?ix)\b(?:carries|carried|holds|held|has|had)\s+(?:the|a|his|her|my)\s+(?:union\s+)?"
        r"delegate(?:'s|’s)\s+card\b"
        r"|\b(?:i\s+am|i'm|is|was|am|has\s+been|have\s+been|became)\s+(?:the|a|our)\s+delegate\s+contact\s+for\s+"
        r"(?:the\s+)?(?:[\w'-]+\s+){0,3}?(?:crews?|workers|staff|shifts?|members|team)\b"), False),
)
_POLICY_BEFORE = re.compile(
    r"(?i)\b(?:may|must|can|cannot|could|should|shall|will|would|if|unless|whether|anyone|everyone"
    r"|any\s+(?:applicant|worker|employee|staff|person|candidate)s?)\b")
_FICTION = re.compile(
    r"(?im)^[ \t#*]*chapter\s+[\w-]+|\b(?:short\s+story|flash\s+fiction|works?\s+of\s+fiction|fictional|the\s+narrator"
    r"|the\s+characters\s+are\s+(?:invented|fictional))\b")
_PREDICATE_END = re.compile(r"(?i)\s+(?:and|but|because)\s")


def indirect_statement_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    # A document that presents itself as fiction switches off only this added vocabulary (fiction statements are hard
    # negatives, taxonomy section 4); the finders still read it as before, so a document cannot lower its protection
    # below 0.4.0's by calling itself a story.
    if _FICTION.search(text):
        return ()
    out = []
    for label, pattern, needs_pronoun in _INDIRECT_PATTERNS:
        for m in pattern.finditer(text):
            start, end = _sentence_clause(text, m.start(), m.end())
            before = text[start:m.start()]
            if _POLICY_BEFORE.search(before) or needs_pronoun and not _PRONOUN.search(text, start, m.end()):
                continue
            start = m.start()
            cut = _PREDICATE_END.search(text, m.end(), end)
            if cut is not None:
                end = cut.start()
            if start < end:
                out.append(_statement(start, end, label, INDIRECT_STATEMENT_STAGE, tier))
    return tuple(out)
