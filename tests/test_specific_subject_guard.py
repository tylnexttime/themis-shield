"""Guard for FAB-01 and FAB-02 (independent review of candidate 0.5): a pass that REMOVES a finding must never remove a
statement about a specific person.

The development-set measurements passed while ``policy-statements`` and the DD-26 change to ``negative-statements``
released statements about the cook, our storeman and "Parents", so a better score cannot be trusted to catch this class
of error. This guard reads every gold sensitive span of every evaluation set (split G: the adjudicated gold) whose
sentence names a person (a gold PERSON span in the sentence) or a definite singular role ("the cook", "our storeman",
"the new groundskeeper"), and asserts that neither pass would drop it.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from themis_shield.types import Label

ROOT = Path(__file__).resolve().parents[1]
_CASES = ROOT / "eval" / "cases"
FILES = sorted(
    p for p in _CASES.glob("*.jsonl") if not p.name.endswith(".adjudicated.jsonl") and not p.name.startswith("test-g-")
) + sorted(_CASES.glob("test-g-*.adjudicated.jsonl"))
SENSITIVE = {label.value for label in (
    Label.HEALTH, Label.DISABILITY, Label.INDIGENOUS, Label.SEXUALITY, Label.RELIGION, Label.CRIMINAL,
    Label.FAMILY_VIOLENCE, Label.ETHNICITY, Label.UNION)}
SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)]*(?=\s)|\n")
# A definite singular role: a determiner, at most two words, then a singular noun that names one person's role.
DEFINITE_ROLE = re.compile(
    r"(?i)\b(?:the|our|this|that|my|your|his|her|their)\s+(?:[a-z][\w'\u2019-]*\s+){0,2}?"
    r"(?:cook|storeman|groundskeeper|officer|manager|supervisor|receptionist|cleaner|foreman|teacher|nurse|driver"
    r"|employee|worker|applicant|candidate|client|patient|resident|student|tenant|volunteer|contractor|apprentice"
    r"|analyst|clerk|technician|coordinator|assistant|director|lead|member|person|individual|child|mother|father"
    r"|son|daughter|husband|wife|partner|boy|girl|man|woman)\b(?!s)")


def _specific_sentences():
    """(case id, text, gold span, sentence span) for every gold sensitive span whose sentence names someone."""
    for path in FILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            text = case["text"]
            for gold in case["spans"]:
                if gold["label"] not in SENSITIVE:
                    continue
                ends = [m.end() for m in SENTENCE_END.finditer(text, 0, gold["start"])]
                after = SENTENCE_END.search(text, gold["end"])
                left, right = ends[-1] if ends else 0, after.start() if after else len(text)
                named = any(s["label"] == "PERSON" and s["start"] < right and left < s["end"] for s in case["spans"])
                if named or DEFINITE_ROLE.search(text, left, right):
                    yield case["id"], text, gold, (left, right)


def test_policy_statements_never_drops_a_gold_statement_about_a_named_person_or_a_definite_role():
    from themis_shield.detectors.policy_statements import policy_statement

    tested, dropped = 0, []
    for case_id, text, gold, sentence in _specific_sentences():
        tested += 1
        for start, end in ((gold["start"], gold["end"]), sentence):
            if policy_statement(text, start, end):  # the wording alone: no PERSON finding to lean on
                dropped.append((case_id, gold["label"], text[start:end][:120]))
    assert dropped == []
    assert tested >= 1000


def test_negative_statements_never_exempts_a_gold_statement_about_a_named_person_or_a_definite_role():
    from themis_shield.detectors import statements as module

    patterns = module._NEGATIVE_PATTERNS + module._NEGATIVE_RECORDS
    tested, exempted = 0, []
    for case_id, text, gold, (left, right) in _specific_sentences():
        for label, pattern in patterns:
            for m in pattern.finditer(text, left, right):
                if not (m.start() < gold["end"] and gold["start"] < m.end()):
                    continue
                tested += 1
                a = m.start()
                while a < m.end() and text[a].isspace():
                    a += 1
                _, end = module._sentence_clause(text, a, m.end())
                if module._generic_subject_statement(text, a, m.end(), end):
                    exempted.append((case_id, gold["label"], text[left:right][:120]))
    assert exempted == []
    assert tested >= 15  # 20 negative matches inside such gold spans when written
