"""Deterministic vetoes for the typed tier's subject check (``[typed] subject_lift``): fail closed, by wording.

The subject check lets a local model decide that a sentence a detector called a sensitive statement is a general rule
about nobody in particular. Two independent reviews showed that wording rules cannot make that decision (DD-32, DD-33:
each rule left a sentence shape that released a statement about one person). So the rules here make no decision to
release. They can only VETO: a sentence one of them fires on keeps its hold, whatever the model says. Every rule errs
towards the veto (an unknown word is read as a name, an unknown definite phrase as someone).

They are a backstop, not a guarantee. They catch the signs of someone in particular that are listed below, not every
sign: ordinary sentence shapes that name one person pass all of them (a role without an article, "Staff must cover
front desk while receptionist has chemo."; a lower-case title, "... tell anyone ceo has hiv."; a numbered person,
"tenant 12"; a list item, "night cook: staff must ..."; independent review HL-07, kept on record in
``tests/test_subject_check.py``). For a sentence none of them vetoes, the model's confident "general rule" under both
option orders and its document-level answer are all that stands between the sentence and a lift (see ``policy.py``),
and a heading has been enough to move the model. That is why the check is off by default.

``sentence_veto`` returns a fixed code (never text) or None:

- ``table-cell``: the finding is a cell of a sensitive-column table (it describes the row's person);
- ``finding``: another finding lies in the sentence (a name, a quasi-identifier, a contact, an identifier, an address,
  a date of birth: anything but a sensitive statement or a plain date);
- ``pronoun``: a personal pronoun (I, you, he, she, we, they and their forms; "they" can be one person);
- ``someone``: an indefinite reference to one person ("someone", "one of", "a colleague", "another");
- ``reported``: narration or reported speech (was, were, had, did, said, told, asked ...): something that happened;
- ``definite``: a definite noun phrase ("the cook", "this placement", "the open ward") unless it is plainly an
  organisation or an abstract thing ("the College", "the People and Capability team", "the check", "the same");
- ``name``: a capitalised word that is not a known sentence opener, a class of people ("Staff", "Applicants"), part of
  an organisation phrase or a calendar or community word ("Dana", "Mum", "Mr Okafor", "Wattle Street", "HIV");
- ``script``: a letter outside the Latin alphabet (these rules read English wording only);
- ``no-class``: the sentence names no class of people ("staff", "applicants", "any employee", "a person", "anyone"):
  a form answer ("No", "Medical history: asthma") or a fragment is not a rule about a class;
- ``no-rule``: the sentence has no rule verb ("can", "may", "must", "is entitled to", "is treated as", "does not
  ask"): "Staff in payroll have been diagnosed with depression." names a class and states a fact about the people in
  it;
- ``located``: a class of people narrowed to one place or time ("staff on nights", "any employee in room 4", "tenants
  in unit 12", "staff with a record in the finance team"): the people there, not everyone of that kind ("at any
  stage", "in all departments", "in the workplace" are not places);
- ``dated``: a point in time ("last week", "yesterday", "this morning", "recently", "since March"): a rule is not
  about last week.
"""

from __future__ import annotations

import re

from ..placeholders import SENSITIVE_LABELS
from ..types import Label
from .statements import TABLE_STATEMENT_STAGE

# Labels that may share a sentence with a liftable statement: other statements (each needs its own answer) and plain
# dates. Anything else in the sentence is a person or a personal value.
_QUIET_LABELS = frozenset(SENSITIVE_LABELS) | {Label.OTHER_DATE}

_PRONOUN = re.compile(
    r"(?i)\b(?:i|me|my|mine|myself|you|your|yours|yourself|yourselves|he|him|his|himself|she|her|hers|herself"
    r"|we|us|our|ours|ourselves|they|them|their|theirs|themselves|themself)\b")
_SOMEONE = re.compile(
    r"(?i)\b(?:someone|somebody|one\s+of|a\s+certain|another|a\s+(?:colleague|friend|neighbou?r|relative|co-?worker"
    r"|workmate|member\s+of\s+(?:my|our|the)))\b")
_REPORTED = re.compile(
    r"(?i)\b(?:was|were|wasn't|weren't|had|hadn't|did|didn't|said|says|told|tells|asked|asks|mentioned|confirmed"
    r"|explained|complained|claimed|disclosed|admitted|reported|emailed|wrote|phoned|rang|went|came|got|took)\b")
_WORD = re.compile(r"[^\W\d_][\w'’-]*")
_THE = frozenset(("the",))
_DEMONSTRATIVE = frozenset(("this", "that", "these", "those"))
# A definite noun phrase ends at one of these (a function word or an auxiliary) or at punctuation.
_STOP = frozenset((
    "a an the this that these those and or but nor so if once when where while whether as of in on at by for to from "
    "with within without under over into onto about after before during until than then per via is are be been being "
    "was were has have had do does did can could may might must shall should will would cannot not no any all each "
    "every some most many who whom whose which what it its there here also only both either neither").split())
# Heads that make a definite phrase an organisation or an abstract thing. Never a person, and never a place where
# particular people are (a ward, a house, a site, a placement). An organisation head is the last word of its phrase.
ORG_HEADS = frozenset((
    "team department unit office branch division committee board council college company university school employer "
    "organisation organization authority agency commission service services program programme policy policies act "
    "award agreement code standard standards scheme plan framework guideline guidelines procedure procedures "
    "handbook manual group desk portal form register legislation law regulations government union business "
    "workplace institute").split())
HARMLESS_HEADS = frozenset((
    "same check checks role roles position positions duties requirements relevant following time basis purpose "
    "extent nature outcome outcomes result results process information matter request requests application "
    "applications job work right rights need needs reason reasons way event stage").split())
# Words for one person. With the suffix test below they make a definite phrase a veto even beside a harmless word
# ("the team cook", "the relevant manager").
ROLE_WORDS = frozenset((
    "cook chef nurse clerk guard lead head boss mum mom dad kid boy girl guy lady man woman child baby wife husband "
    "son daughter mate friend colleague neighbour neighbor temp apprentice trainee applicant candidate assistant "
    "attendant resident student patient client tenant parent carer member person individual employee one "
    "delegate steward rep representative volunteer contractor pilot cadet recruit intern agent customer worker "
    "manager supervisor officer teacher driver starter visitor user learner jobseeker").split())
_ROLE_SUFFIX = re.compile(r"[a-z]{2,}(?:er|or|ist|ian|man|woman|ee|ess)")
# Capitalised words that name a class of people, never one person (beside the plurals of ROLE_WORDS).
_CLASS_WORDS = frozenset("staff people everyone anyone anybody everybody nobody personnel".split())
# Capitalised words that are not names: calendar words and community terms that are capitalised by convention.
_CAPITAL_OK = frozenset((
    "monday tuesday wednesday thursday friday saturday sunday january february march april may june july august "
    "september october november december aboriginal torres strait islander indigenous australian australia "
    "first nations english christmas easter ramadan").split())
_UPPER_OK = frozenset("INFO WARN WARNING ERROR DEBUG NOTICE TRACE FAQ FAQS Q A HR OK ID IT PDF URL API UTC EAP WHS OHS".split())
# Words that may open a sentence, a quotation or a list item with a capital letter (beside stop words, classes of
# people and organisation or harmless heads). Anything else capitalised there is read as a name.
_OPENERS_OK = frozenset((
    "note notes notice notices please reasonable adjustments disclosures requests questions answer answers question generally usually "
    "normally typically where under during unless because however therefore employment membership participation "
    "support access leave flexible confidential personal private medical religious cultural criminal police working "
    "new current eligible casual permanent full part trade industrial equal general specific further additional "
    "other such these those there this that it reminder accessibility ref refs").split())
# A class of people in the singular: "any employee", "a person", "no applicant", "each new starter".
_CLASS_SINGULAR = re.compile(r"(?i)\b(?:a|an|any|every|each|no)\s+(?:[a-z][a-z-]*\s+){0,2}?([a-z][a-z-]*)\b")
_CLASS_PRONOUN = re.compile(r"(?i)\b(?:anyone|anybody|everyone|everybody|nobody|no\s+one|those\s+(?:who|with))\b")
# A rule verb: a modal, or a normative construction. Lower case only (a "May" is a month). A sentence without one
# states a fact, and a fact about a class of people in one place is a statement about those people.
_RULE_VERB = re.compile(
    r"\b(?:can|could|may|might|must|should|shall|will|cannot|can't|need\s+not"
    r"|(?:is|are)\s+(?:(?:not|never|always|also|only)\s+)?(?:entitled|eligible|required|expected|able|allowed|permitted"
    r"|welcome|free|encouraged|treated|assessed|governed|handled|regarded|considered|kept|held|recorded|covered"
    r"|protected|managed|available|asked|invited|supported|offered)"
    r"|(?:does|do)\s+not\s+(?:[a-z]+\s+)?(?:ask|require|record|collect|keep|disqualify|prevent|exclude|bar|affect"
    r"|need|have\s+to)"
    r"|appl(?:y|ies)\s+to)\b")
# A class of people followed (within three words) by one of these and then anything but a quantifier or a stock phrase
# is a group in one place or time.
_LOCATING = frozenset(("in", "on", "at", "from", "near", "inside", "outside"))
_NOT_A_PLACE = frozenset((
    "any all every each either both general particular principle practice employment writing advance addition "
    "accordance line full part time need good work").split())
# A locating word and a definite phrase anywhere in the sentence ("in the finance team", "at this depot", "on the night
# roster") is one place, unless the phrase is the whole organisation or an abstract thing ("in the workplace", "at the
# time", "in the same way").
_DEFINITE_PLACE = re.compile(r"(?i)\b(?:in|on|at|from|within|inside|outside|near)\s+(?:the|this|that|these|those)\b")
_WHOLE_ORGANISATION = frozenset((
    "organisation organization company college university school institute council authority agency business "
    "workplace employer government sector industry law act legislation policy award agreement code").split())
# A point in time. Lower case for the bare words; a rule has no "last week".
_DATED = re.compile(
    r"\b(?:last\s+(?:week|month|year|night|shift|term|friday|monday|tuesday|wednesday|thursday|saturday|sunday)"
    r"|yesterday|today|tonight|tomorrow|recently|currently|this\s+(?:morning|afternoon|evening|week|month|year|term)"
    r"|at\s+present|so\s+far|since\s+(?:[A-Z][a-z]+|\d{4})|\d+\s+(?:days?|weeks?|months?|years?)\s+ago)\b")
_JOINERS = frozenset(("and", "of", "for", "&"))
_OPENING = "\"'“‘([{:*•>#|–—"


def _singular(low: str) -> str:
    return low[:-1] if low.endswith("s") and not low.endswith("ss") else low


def _role(word: str) -> bool:
    low = word.casefold()
    if low.endswith(("'s", "’s")):
        low = low[:-2]
    if low in ORG_HEADS or low in HARMLESS_HEADS or low in _STOP:
        return False
    return low in ROLE_WORDS or bool(_ROLE_SUFFIX.fullmatch(low))


def _class_word(low: str, listed: bool = False) -> bool:
    """A class of people in the plural ("applicants", "carers", "staff"): never one person. ``listed`` takes only
    the plurals of ROLE_WORDS (for the test that a sentence names a class at all, where a guess must not count)."""
    if low in _CLASS_WORDS:
        return True
    if not low.endswith("s") or low.endswith("ss"):
        return False
    return low[:-1] in ROLE_WORDS or (not listed and bool(_ROLE_SUFFIX.fullmatch(low[:-1])))


def _phrase(text: str, tokens, index: int) -> list[int]:
    """Token indexes of the noun phrase that starts at ``tokens[index]``: up to five words, ending at punctuation or
    a stop word ("and", "of", "for" continue it only between two capitalised words: "People and Capability")."""
    out = []
    for at in range(index, min(index + 5, len(tokens))):
        start, _, word = tokens[at]
        if at > index and re.search(r"[^\s\w&'’-]", text[tokens[at - 1][1]:start]):
            break  # punctuation between the words
        low = word.casefold()
        if low in _STOP:
            joined = (low in _JOINERS and at > index and at + 1 < len(tokens)
                      and tokens[at - 1][2][0].isupper() and tokens[at + 1][2][0].isupper()
                      and not text[tokens[at][1]:tokens[at + 1][0]].strip())
            if not joined:
                break
        out.append(at)
    return out


def _thing(tokens, phrase: list[int]) -> bool:
    """The phrase is an organisation or an abstract thing, not someone: its last word is an organisation head and no
    lower-case word in it is a word for one person ("the People and Capability team", "the College", but not "the team
    cook"); or it holds a harmless head and no word for a person at all ("the same", "the relevant instruments")."""
    words = [tokens[at][2] for at in phrase if tokens[at][2].casefold() not in _JOINERS]
    if not words:
        return False
    if words[-1].casefold() in ORG_HEADS:
        return not any(_role(word) for word in words if not word[0].isupper())
    return any(word.casefold() in HARMLESS_HEADS for word in words) and not any(_role(word) for word in words)


def _opens(text: str, tokens, at: int) -> bool:
    """The token opens the sentence, a quotation, a list item or the part after a colon."""
    if at == 0 or tokens[at - 1][2] in _UPPER_OK:
        return True  # the first word, or the first after a log level or a "Q" / "A" marker
    gap = text[tokens[at - 1][1]:tokens[at][0]]
    return any(char in _OPENING for char in gap) or bool(re.search(r"[.!?]\s", gap))


def _names_a_class(sentence: str) -> bool:
    if _CLASS_PRONOUN.search(sentence):
        return True
    if any(_class_word(m.group().casefold(), listed=True) for m in _WORD.finditer(sentence)):
        return True
    return any(m.group(1).casefold() in ROLE_WORDS for m in _CLASS_SINGULAR.finditer(sentence))


def wording_veto(text: str, start: int, end: int) -> str | None:
    """The wording vetoes for ``text[start:end]`` (one sentence): a fixed code, or None when nothing in the wording
    points to someone in particular."""
    sentence = text[start:end]
    if any(char.isalpha() and ord(char) > 0x024F for char in sentence):
        return "script"
    if not _names_a_class(sentence):
        return "no-class"
    if not _RULE_VERB.search(sentence):
        return "no-rule"
    if _PRONOUN.search(sentence):
        return "pronoun"
    if _SOMEONE.search(sentence):
        return "someone"
    if _REPORTED.search(sentence):
        return "reported"
    if _DATED.search(sentence):
        return "dated"
    tokens = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(text, start, end)]
    for match in _DEFINITE_PLACE.finditer(sentence):
        after = next((k for k, token in enumerate(tokens) if token[0] >= start + match.end()), None)
        phrase = [] if after is None else _phrase(text, tokens, after)
        words = [tokens[k][2].casefold() for k in phrase]
        if not any(word in HARMLESS_HEADS or word in _WHOLE_ORGANISATION for word in words):
            return "located"
    for at, (_, _, word) in enumerate(tokens):
        low = word.casefold()
        if not (_class_word(low, listed=True) or low in ROLE_WORDS):
            continue
        for k in range(at + 1, min(at + 4, len(tokens) - 1)):
            if re.search(r"[^\s\w'’-]", text[tokens[k - 1][1]:tokens[k][0]]):
                break  # punctuation ends the phrase
            following = tokens[k + 1][2].casefold()
            if following in _THE or following in _DEMONSTRATIVE:
                break  # a definite phrase: read above, where the whole organisation is not a place
            if tokens[k][2].casefold() in _LOCATING and following not in _NOT_A_PLACE:
                return "located"
    excused: set[int] = set()
    for at, (_, _, word) in enumerate(tokens):
        low = word.casefold()
        if low not in _THE and low not in _DEMONSTRATIVE:
            continue
        phrase = _phrase(text, tokens, at + 1)
        if low in _DEMONSTRATIVE and not phrase:
            continue  # "that" before a clause, "this" as a pronoun: no noun phrase to read
        if low == "that" and not _role(tokens[phrase[0]][2]):
            continue  # "note that adjustments are ...": a clause; what follows is still read for names below
        if not _thing(tokens, phrase):
            return "definite"
        excused.update(phrase)
    for at, (_, _, word) in enumerate(tokens):
        if not word[0].isupper() or at in excused:
            continue
        if word.isupper() and len(word) > 1:
            if word in _UPPER_OK:
                continue
            return "name"
        low = word.casefold()
        if low in _CAPITAL_OK or _singular(low) in _CAPITAL_OK or _class_word(low) or word in _UPPER_OK:
            continue
        # A capitalised organisation phrase without "the" ("Employee Assistance Program", "Policy reminder"): a run of
        # capitalised words that ends in an organisation head, or is followed by one in lower case.
        run = [k for k in _phrase(text, tokens, at)]
        while run and not (tokens[run[-1]][2][0].isupper() or tokens[run[-1]][2].casefold() in ORG_HEADS):
            run.pop()
        capitals = [k for k in run if tokens[k][2][0].isupper() or tokens[k][2].casefold() in _JOINERS]
        if run and tokens[run[-1]][2].casefold() in ORG_HEADS and capitals == run[:len(capitals)] and len(run) - len(capitals) <= 1:
            excused.update(run)
            continue
        if _opens(text, tokens, at) and (
            low in _STOP or low in _OPENERS_OK or low in ORG_HEADS or low in HARMLESS_HEADS
            or _singular(low) in HARMLESS_HEADS or _singular(low) in ORG_HEADS
        ):
            continue
        return "name"
    return None


def sentence_veto(text: str, start: int, end: int, findings) -> str | None:
    """Why the statement in ``text[start:end]`` can never be read as a general rule (a fixed code), or None."""
    for item in findings:
        if not (item.start < end and start < item.end):
            continue
        if any(entry.stage == TABLE_STATEMENT_STAGE for entry in item.evidence):
            return "table-cell"
        if item.labels - _QUIET_LABELS:
            return "finding"
    return wording_veto(text, start, end)


__all__ = ["sentence_veto", "wording_veto"]
