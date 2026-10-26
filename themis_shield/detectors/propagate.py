"""Deterministic table, name-token and initials propagation.

"""

from __future__ import annotations

import csv
from dataclasses import replace
import re

from ..types import Label, Span
from .contracts import DetectionFinding, FindingEvidence, finding
from .settings import fix_on, v02
from .union import union_findings


STOP = {
    "the", "this", "that", "dear", "hi", "hello", "thanks", "regards", "kind",
    "mr", "mrs", "ms", "dr", "miss", "mx", "prof", "and", "of", "de", "van",
    "von", "la", "le", "da", "di", "del", "student", "teacher",
}
HDR_WORDS = (
    r"(full|first|given|preferred|last|family|sur|legal|known[\s_]*as|other)?"
    r"[\s_\-/]*(name|names|surname|family)"
)
NAME_HDR = re.compile(
    r"(?i)^\s*(" + HDR_WORDS
    + r"|surname|given|preferred|learner([\s_]*name)?|student[\s_]*name|stu[\s_]*name|"
    r"staff[\s_]*name|contact([\s_]*name)?|emergency[\s_]*contact|teacher|trainer|"
    r"assessor|supervisor|employer[\s_]*contact|surname[\s_]*/[\s_]*family|"
    r"family[\s_]*/[\s_]*surname|given[\s_]*/[\s_]*first|family[\s_]*/[\s_]*last)\s*$"
)
SEPS = (",", "\t", ";", "|")
WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)


def _cells(line: str, sep: str) -> list[tuple[int, int]]:
    """CSV validation plus a quote-aware original-position walk.

    Unlike a decoded-value `find`, doubled quotes and delimiters inside
    quoted cells cannot move offsets into another column. Multiline quoted
    records remain unsupported and fail closed in a recognised table block.
    """
    values = next(csv.reader([line], delimiter=sep, strict=True))
    bounds, first, position = [], 0, 0
    quoted = False
    while position < len(line):
        character = line[position]
        if character == '"':
            if quoted and position + 1 < len(line) and line[position + 1] == '"':
                position += 2
                continue
            quoted = not quoted
        if character == sep and not quoted:
            bounds.append((first, position))
            first = position + 1
        position += 1
    if quoted:
        raise ValueError("multiline-csv-not-supported")
    bounds.append((first, len(line)))
    if len(bounds) != len(values):
        raise ValueError("invalid-csv-offsets")
    out = []
    for a, b in bounds:
        while a < b and line[a].isspace():
            a += 1
        while a < b and line[b - 1].isspace():
            b -= 1
        if b - a >= 2 and line[a] == line[b - 1] == '"':
            a, b = a + 1, b - 1
        out.append((a, b))
    return out


def table_spans(
    text: str, stage: str = "propagate-tier1", tier: int = 1
) -> tuple[DetectionFinding, ...]:
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    out, index = [], 0
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        bom = len(raw) - len(raw.lstrip("\ufeff"))
        header = raw[bom:]
        separator = max(SEPS, key=header.count)
        if header.count(separator) < 1:
            index += 1
            continue
        try:
            cells = _cells(header, separator)
        except (csv.Error, ValueError, StopIteration):
            # Not a recognised header. No inferred table action yet.
            index += 1
            continue
        columns = {
            column for column, (a, b) in enumerate(cells)
            if NAME_HDR.fullmatch(header[a:b].strip())
        }
        if not columns:
            index += 1
            continue
        row_index = index + 1
        while row_index < len(lines):
            row = lines[row_index].rstrip("\r\n")
            if row.replace('""', "").count('"') % 2:
                # An unbalanced quote inside a recognised name table is a multiline cell: the
                # names in it cannot be located, so fail closed rather than end the table quietly.
                raise ValueError("incomplete-person-table")
            if not row.strip() or separator not in row:
                break
            try:
                row_cells = _cells(row, separator)
            except (csv.Error, ValueError, StopIteration):
                raise ValueError("incomplete-person-table") from None
            for column, (a, b) in enumerate(row_cells):
                value = row[a:b]
                if (
                    column in columns and value.strip()
                    and not re.fullmatch(r"[\d\s\-./]+", value)
                ):
                    a += len(value) - len(value.lstrip())
                    b -= len(value) - len(value.rstrip())
                    out.append(finding(
                        offsets[row_index] + a, offsets[row_index] + b,
                        Label.PERSON, stage, tier,
                    ))
            row_index += 1
        index = row_index
    return tuple(out)


# v0.3 fix "title-tokens": titles, honorifics and ranks in front of a name. A found name such as "Captain Jane Roe"
# keeps its whole span, but the leading title is never propagated through the document as a name token: the title is
# beside the name, not part of it. Only a capitalised title before at least one other name token, with no comma after
# it, is skipped ("Bishop, Jane" keeps "Bishop": surname-first lists put the surname there).
TITLE_WORDS = frozenset({
    "captain", "capt", "doctor", "professor", "sister", "brother", "father", "mother", "reverend", "rev", "pastor",
    "elder", "deacon", "bishop", "rabbi", "imam", "saint", "sergeant", "sgt", "constable", "inspector", "detective",
    "officer", "lieutenant", "lt", "major", "colonel", "col", "general", "admiral", "commander", "corporal",
    "private", "judge", "justice", "magistrate", "councillor", "cr", "senator", "minister", "mayor", "president",
    "director", "manager", "coordinator", "head", "lead", "chief", "chair", "chairperson", "nurse", "coach",
    "uncle", "aunt", "aunty", "auntie", "sir", "dame", "lady", "lord", "madam", "madame", "mister", "master",
    "fr", "sr", "br", "dean", "principal", "matron", "chef", "governor", "premier", "sheriff", "agent",
})


def _leading_title(name: str, parts: list[str]) -> str | None:
    """The first name part when it is a capitalised title followed by another part and no comma."""
    if len(parts) < 2 or parts[0].casefold() not in TITLE_WORDS or not parts[0][:1].isupper():
        return None
    after = name[name.find(parts[0]) + len(parts[0]):]
    return None if after.lstrip().startswith(",") else parts[0]


def extra_spans(
    text: str,
    spans: tuple[DetectionFinding, ...],
    stage: str = "propagate-tier1",
    tier: int = 1,
) -> tuple[DetectionFinding, ...]:
    out = []
    for parent in spans:
        if Label.PERSON not in parent.labels:
            continue
        name = text[parent.start:parent.end]
        parts = [part for part in WORD.findall(name) if part.casefold() not in STOP]
        if fix_on("title-tokens"):
            title = _leading_title(name, parts)
            if title is not None:
                parts = parts[1:]
        tokens = {part for part in parts if len(part) >= 2}
        capitals = [part for part in parts if part[:1].isupper()]
        initials = set()
        if len(capitals) >= 2:
            initial = "".join(part[0] for part in capitals)
            initials.update({initial, ".".join(initial) + ".", " ".join(initial)})
        parent_evidence = tuple(
            item for item in parent.evidence if item.label == Label.PERSON
        )
        evidence = parent_evidence + (
            FindingEvidence(Label.PERSON, stage, tier),
        )
        for token, flags in [
            *((token, re.I | re.UNICODE) for token in sorted(tokens)),
            *((token, re.UNICODE) for token in sorted(initials)),
        ]:
            for match in re.finditer(
                r"(?<!\w)" + re.escape(token) + r"(?!\w)", text, flags
            ):
                out.append(replace(
                    parent,
                    span=Span(match.start(), match.end()),
                    labels=frozenset({Label.PERSON}),
                    primary_label=Label.PERSON,
                    detector_id=stage,
                    raw_label=None,
                    checksum_valid=None,
                    statement_span=None,
                    evidence=evidence,
                ))
    return tuple(out)


# ----------------------------------------------------------------------------------------------------------------------
# v0.2: column-aware quasi-identifier pass for tables.
#
# A table that carries identifier-like columns (date of birth, age, postcode, dates) or is labelled anonymised or
# de-identified can single a person out through a rare categorical value: the only "Fleet controller" next to a
# redacted DOB and postcode is still one person. In such tables, a value of a categorical column (role, title, team,
# site, attribute and close synonyms) that occurs at most QUASI_MAX_COUNT times in its column becomes QUASI_ID.
# Deterministic and bounded; header vocabulary only, no learned model and nothing from any evaluation set.
# ----------------------------------------------------------------------------------------------------------------------
TABLE_QUASI_STAGE = "table-quasi"
QUASI_MAX_COUNT = 2
QUASI_MAX_ROWS = 5000
QUASI_MAX_VALUE_CHARS = 80
QUASI_CONTEXT_LINES = 3
IDENTIFIER_HDR = re.compile(
    r"(?i)\b(?:dob|d\s?o\s?b|date\s?of\s?birth|birth\s?date|birthdate|birthday|born|birth\s?year|"
    r"year\s?of\s?birth|yob|age|ages|age\s?band|age\s?group|age\s?range|postcode|postcodes|post\s?code|"
    r"postal\s?code|zip|zipcode|zip\s?code|date|dates)\b"
)
CATEGORY_HDR = re.compile(
    r"(?i)\b(?:role|roles|job|job\s?title|title|position|occupation|profession|team|department|dept|division|"
    r"section|unit|site|campus|location|branch|office|workplace|attribute|attributes|group|shift|grade|gender|"
    r"sex|language|nationality|country)\b"
)
ANONYMISED = re.compile(
    r"(?i)\b(?:anonymi[sz]ed|anonymous|de-?identified|deidentified|pseudonymi[sz]ed|names?\s+removed|"
    r"names?\s+redacted|identifiers?\s+removed)\b"
)
_ALIGN_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_NOT_CATEGORICAL = re.compile(r"[\d\s\-./:,%$+()#]*")


def _header_name(raw: str) -> str:
    return re.sub(r"[\s_\-]+", " ", raw).strip()


# v0.3 fix "table-role-words": a cell that is only a common occupation or role title is never a quasi-identifier
# candidate. A common job title next to a substituted date of birth and postcode adds little: its holders are
# everywhere. What singles a person out is a rare or qualified role ("Only overseas-trained surgeon", "Night
# pharmacist (only one)", "Branch manager"), and those stay candidates: the match is on the whole cell, so any
# qualifier (rank, place, shift, "only", "sole", a count) keeps the cell in the pass. The list is a fixed vocabulary
# of common occupations and generic role words, in singular and plural forms; it is not learned from any data.
COMMON_ROLES = frozenset(" ".join(value.split()) for value in """
accountant|accounts clerk|accounts officer|accounts payable officer|administration officer|administrative officer|
administration assistant|administrative assistant|admin officer|admin assistant|administrator|analyst|
apprentice|assistant|bar attendant|barista|bookkeeper|bricklayer|builder|bus driver|business analyst|butcher|
call centre operator|carer|aged care worker|disability support worker|support worker|care worker|carpenter|
cashier|checkout operator|chef|child care worker|childcare educator|educator|cleaner|clerk|general clerk|
office clerk|data entry operator|receptionist|consultant|contractor|cook|kitchen hand|kitchenhand|
customer service officer|customer service representative|customer service assistant|delivery driver|developer|
software developer|software engineer|programmer|web developer|doctor|general practitioner|driver|truck driver|
electrician|employee|engineer|civil engineer|mechanical engineer|electrical engineer|farmhand|farm hand|
farm worker|gardener|groundskeeper|hairdresser|handyperson|labourer|general labourer|construction worker|lawyer|
solicitor|librarian|mechanic|motor mechanic|diesel mechanic|nurse|registered nurse|enrolled nurse|
nursing assistant|office manager|operator|machine operator|forklift driver|forklift operator|painter|
paramedic|personal assistant|pharmacist|pharmacy assistant|physiotherapist|plumber|police officer|
project officer|project manager|project coordinator|retail assistant|sales assistant|sales associate|
sales representative|salesperson|secretary|security guard|security officer|staff|staff member|storeperson|
storeman|student|supervisor|team member|team leader|teacher|primary teacher|secondary teacher|teacher aide|
teachers aide|teacher's aide|technician|it technician|lab technician|laboratory technician|tradesperson|trainee|
tutor|volunteer|waiter|waitress|waitstaff|warehouse assistant|warehouse worker|warehouse operator|welder|worker|
officer|manager|coordinator|trainer|lecturer|casual|full-time|part-time|permanent|temporary|
""".replace("\n", "").split("|") if value.strip())


def common_role(value: str) -> bool:
    """The whole cell is a common occupation or role word (case, spacing and a plural "s" ignored)."""
    key = " ".join(value.split()).casefold()
    return key in COMMON_ROLES or (key.endswith("s") and key[:-1] in COMMON_ROLES)


def table_quasi_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    out, index = [], 0
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        header = raw.lstrip("﻿")
        separator = max(SEPS, key=header.count)
        if header.count(separator) < (2 if separator == "|" else 1):
            index += 1
            continue
        try:
            cells = _cells(header, separator)
        except (csv.Error, ValueError, StopIteration):
            index += 1
            continue
        names = [_header_name(header[a:b]) for a, b in cells]
        identifier = {j for j, name in enumerate(names) if name and IDENTIFIER_HDR.search(name)}
        category = {
            j for j, name in enumerate(names)
            if name and CATEGORY_HDR.search(name) and j not in identifier
        }
        if not category:
            index += 1
            continue
        rows, row_index = [], index + 1
        while row_index < len(lines) and len(rows) < QUASI_MAX_ROWS:
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
            if len(row_cells) != len(cells):
                break
            rows.append((row_index, row, row_cells))
            row_index += 1
        context = "".join(lines[max(0, index - QUASI_CONTEXT_LINES):index + 1])
        role_words = fix_on("table-role-words")
        if rows and (identifier or ANONYMISED.search(context)):
            for column in sorted(category):
                values = []
                for line_no, row, row_cells in rows:
                    a, b = row_cells[column]
                    value = row[a:b]
                    if (
                        not value.strip() or len(value) > QUASI_MAX_VALUE_CHARS
                        or _NOT_CATEGORICAL.fullmatch(value)
                        or not any(ch.isalpha() for ch in value)
                        or (role_words and common_role(value))
                    ):
                        continue
                    values.append((offsets[line_no] + a, offsets[line_no] + b,
                                   " ".join(value.split()).casefold()))
                counts = {}
                for _, _, key in values:
                    counts[key] = counts.get(key, 0) + 1
                for start, end, key in values:
                    if counts[key] <= QUASI_MAX_COUNT:
                        out.append(finding(start, end, Label.QUASI_ID, TABLE_QUASI_STAGE, tier))
        index = row_index if rows else index + 1
    return tuple(out)


def propagate(
    text: str,
    spans: tuple[DetectionFinding, ...],
    stage: str = "propagate-tier1",
    tier: int = 1,
) -> tuple[DetectionFinding, ...]:
    base = union_findings(spans, table_spans(text, stage, tier))
    if v02():
        base = union_findings(base, table_quasi_spans(text, tier))
    return union_findings(base, extra_spans(text, base, stage, tier))
