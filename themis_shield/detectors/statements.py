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
