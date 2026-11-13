"""Deterministic table, name-token and initials propagation.

"""

from __future__ import annotations

from bisect import bisect_left
import csv
from dataclasses import replace
import re

from ..normalise import DetectionView, detection_view
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


# v0.3 fix "title-tokens": honorifics and ranks in front of a name. A found name such as "Captain Jane Roe" keeps its
# whole span, but a leading honorific is never propagated through the document as a name token: the title is beside
# the name, not part of it. Only words that are not used as given names are listed, so no name token is ever lost
# (review fix, v0.3 freeze: "Dean", "Major", "Justice", "Sister", "Chief", "Lady", "Agent", "Chef", "Coach" and
# role words such as "Head" were removed: they are given names too, or not honorifics). A listed word is skipped
# only when it is capitalised, comes first, is followed by another name part and has no comma after it.
TITLE_WORDS = frozenset({
    "captain", "capt", "doctor", "professor", "reverend", "rev", "revd", "father", "fr", "brother", "br", "pastor",
    "rabbi", "imam", "sergeant", "sgt", "constable", "inspector", "insp", "detective", "det", "lieutenant", "lt",
    "colonel", "col", "admiral", "corporal", "cpl", "councillor", "cllr", "cr", "senator", "mister", "madam",
    "madame", "sir", "dame", "hon",
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


# v0.4 fix "column-identifiers": a table column whose header names a personal identifier (a licence, passport,
# Medicare, tax file, national ID, bank account or card number, or a staff, employee, student, member, customer,
# client or patient number) is protected cell by cell. On split D, 21 of the 28 released spans were bare licence
# numbers in such a column: no rule matches an 8-digit licence number, and the finders skipped the column while
# covering every other one. A cell is taken when it carries a digit and is not a date. Headers that name an
# organisation's own identifiers (incident, order, invoice, asset, ticket, case, reference, transaction, product),
# a row counter ("#", "row", "line") or a property of the identifier (type, class, state, status, expiry, issue
# date, balance, limit) are never identifier columns. A bare "ID", "No" or "Number" counts only in a table that also
# has a name column or another personal-identifier column. Header vocabulary only; nothing learned from any data.
TABLE_IDENTIFIER_STAGE = "table-identifiers"
TABLE_IDENTIFIER_MAX_CELL = 40
_ID_SUFFIX = r"(?:\s?(?:id|ids|no|nos|num|number|numbers|code|#))?"
_ID_COLUMNS = (
    (Label.DRIVER_LICENCE, re.compile(r"(?:drivers?\s?)?licen[cs]e" + _ID_SUFFIX + r"|dl" + _ID_SUFFIX
                                      + r"|driver\s?licen[cs]e" + _ID_SUFFIX)),
    (Label.PASSPORT, re.compile(r"passport" + _ID_SUFFIX)),
    (Label.MEDICARE, re.compile(r"medicare" + _ID_SUFFIX)),
    (Label.TFN, re.compile(r"tfn|tax\s?file" + _ID_SUFFIX)),
    (Label.NATIONAL_ID, re.compile(r"national\s?(?:id|identifier|insurance)" + _ID_SUFFIX + r"|ssn|nino")),
    (Label.BANK_ACCOUNT, re.compile(r"(?:bank\s?)?(?:account|acct|acc)" + _ID_SUFFIX + r"|bsb(?:\s?account)?"
                                    + _ID_SUFFIX + r"|iban")),
    (Label.PAYMENT_CARD, re.compile(r"(?:credit\s?|debit\s?|payment\s?)?card" + _ID_SUFFIX)),
    (Label.INTERNAL_ID, re.compile(r"(?:staff|employee|emp|student|member|membership|customer|client|patient|worker"
                                   r"|person|user|union\s?member)\s?(?:id|ids|no|nos|num|number|numbers|code|#)"
                                   r"|empid|staffid|memberid|custid|clientid")),
)
_BARE_ID = re.compile(r"id|ids|no|nos|num|number|identifier")
_NOT_ID_COLUMN = re.compile(
    r"\b(?:incident|order|po|purchase|invoice|inv|asset|ticket|case|ref|reference|transaction|txn|product|item|"
    r"sku|serial|batch|room|row|line|page|version|phone|mobile|tel|fax|date|dates|expiry|expires|expiration|issued|"
    r"issue|class|type|state|status|count|total|amount|balance|limit|name|names|holder)\b"
)
_DATE_CELL = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")


def _identifier_label(name: str) -> Label | None:
    key = " ".join(name.casefold().replace("_", " ").replace("-", " ").replace(".", " ").split())
    if not key or _NOT_ID_COLUMN.search(key):
        return None
    for label, pattern in _ID_COLUMNS:
        if pattern.fullmatch(key):
            return label
    return None


def table_identifier_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    out, index = [], 0
    while index < len(lines):
        header = lines[index].rstrip("\r\n").lstrip("﻿")
        separator = max(SEPS, key=header.count)
        if header.count(separator) < (2 if separator == "|" else 1) or any(ch.isdigit() for ch in header):
            index += 1
            continue
        try:
            cells = _cells(header, separator)
        except (csv.Error, ValueError, StopIteration):
            index += 1
            continue
        names = [header[a:b].strip() for a, b in cells]
        columns = {j: label for j, name in enumerate(names) if (label := _identifier_label(name)) is not None}
        has_name = any(NAME_HDR.fullmatch(name) for name in names if name)
        if columns or has_name:
            for j, name in enumerate(names):
                key = " ".join(name.casefold().replace("_", " ").split())
                if j not in columns and _BARE_ID.fullmatch(key):
                    columns[j] = Label.INTERNAL_ID
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
                # An unquoted comma in a free-text cell ("ROE, Jane") shifts every later cell. Which cell moved is
                # unknown, so both candidates are protected: a little over-redaction, never a skipped identifier.
                for position in sorted({column, column + extra}):
                    a, b = row_cells[position]
                    value = row[a:b]
                    if (
                        not value or len(value) > TABLE_IDENTIFIER_MAX_CELL
                        or not any(ch.isdigit() for ch in value) or _DATE_CELL.fullmatch(value)
                    ):
                        continue
                    out.append(finding(offsets[row_index] + a, offsets[row_index] + b, label,
                                       TABLE_IDENTIFIER_STAGE, tier))
            row_index += 1
        index = row_index if row_index > index + 1 else index + 1
    return tuple(out)


# v0.4 fix "location-columns": a table column whose header names where a person lives ("residence_postcode", "home
# suburb", "postcode of residence", "home address", "residential town", "place of residence", "lives in") is
# protected as ADDRESS, every non-empty cell. On split F an "anonymised" leavers table was released with its nine bare
# four-digit residence postcodes in clear while the dates and role descriptions beside them were protected: no rule
# matches a bare postcode and the finders read it as a number. A header that names a workplace, site, office, branch,
# depot, campus, ward, delivery, return or business location is never a residence column, and a bare "postcode" or
# "suburb" is not either (it is often a site's): the header must carry a residence word. Header words only (split on
# underscores, hyphens, spaces and camel case); nothing learned from any data. The vocabulary is the three sets below.
TABLE_LOCATION_STAGE = "table-locations"
LOCATION_MAX_HEADER_WORDS = 5
_HOME_WORDS = frozenset({"home", "residence", "residential"})
_PLACE_WORDS = frozenset({"address", "postcode", "zip", "zipcode", "suburb", "town", "city", "locality"})
_NOT_HOME_WORDS = frozenset({
    "work", "workplace", "employer", "business", "site", "office", "branch", "depot", "campus", "ward", "delivery",
    "shipping", "return", "email",
})


def _residence_column(name: str) -> bool:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name).casefold()
    words = re.sub(r"\b(?:post|postal|zip) code\b", "postcode", " ".join(re.findall(r"[a-z0-9]+", spaced))).split()
    if not words or len(words) > LOCATION_MAX_HEADER_WORDS or _NOT_HOME_WORDS.intersection(words):
        return False
    return (
        bool({"lives", "resides"}.intersection(words)) or words == ["residence"]
        or ("place" in words and "residence" in words)
        or (bool(_HOME_WORDS.intersection(words)) and bool(_PLACE_WORDS.intersection(words)))
    )


def table_location_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    out, index = [], 0
    while index < len(lines):
        header = lines[index].rstrip("\r\n").lstrip("﻿")
        separator = max(SEPS, key=header.count)
        if header.count(separator) < (2 if separator == "|" else 1) or any(ch.isdigit() for ch in header):
            index += 1
            continue
        try:
            cells = _cells(header, separator)
        except (csv.Error, ValueError, StopIteration):
            index += 1
            continue
        columns = [j for j, (a, b) in enumerate(cells) if _residence_column(header[a:b])]
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
            # An unquoted comma in free text ("12 Wattle St, Redgum VIC 3395", "Yes, both") splits a cell, and every
            # later cell moves. A piece that follows the comma with a space belongs to the cell before it; when that
            # accounts for every extra cell the columns are known. Otherwise any cell the column could be in is
            # protected: a little over-redaction, never a skipped residence.
            groups = []
            for position, (a, b) in enumerate(row_cells):
                if groups and row[row_cells[position - 1][1]:a][-1:].isspace():
                    groups[-1].append(position)
                else:
                    groups.append([position])
            for column in columns:
                for position in groups[column] if len(groups) == len(cells) else range(column, column + extra + 1):
                    a, b = row_cells[position]
                    if any(ch.isalnum() for ch in row[a:b]):
                        out.append(finding(offsets[row_index] + a, offsets[row_index] + b, Label.ADDRESS,
                                           TABLE_LOCATION_STAGE, tier))
            row_index += 1
        index = row_index if row_index > index + 1 else index + 1
    return tuple(out)


# v0.5 fix "unique-roles" (from split F): a role the text says only one person holds ("the only analyst on the overnight
# roster", "the one contractor seconded from the Perth office", "Sole night-shift pharmacist at the district hospital")
# singles that person out, in prose as in a table cell. Matched: "the only", "only", "sole", "the sole" or "the one",
# up to three describing words, a role word, then where or who ("on", "at", "in", "from", "for", "with", "based",
# "seconded", "who"); the span runs to the end of that phrase (punctuation, a cell separator, an auxiliary verb or
# ``UNIQUE_ROLE_MAX_TAIL`` characters; a "who" clause that is still running at that cut goes on to the auxiliary verb
# or the end of the cell or line that follows it, if within ``UNIQUE_ROLE_MAX_RELATIVE`` characters). The role words
# are the last words of ``COMMON_ROLES`` without the generic ones below; a describing word may not be a function word
# or a number ("only by the screening officer", "only one manager on duty" are not matched), and "one of the nurses"
# never is. Fixed vocabulary only, nothing learned from any data.
UNIQUE_ROLE_STAGE = "unique-roles"
UNIQUE_ROLE_MAX_TAIL = 50
UNIQUE_ROLE_MAX_RELATIVE = 120
_GENERIC_ROLE_HEADS = frozenset({
    "casual", "full-time", "part-time", "permanent", "temporary", "hand", "staff", "member", "employee", "student",
    "volunteer",
})
_ROLE_HEADS = sorted({value.split()[-1] for value in COMMON_ROLES} - _GENERIC_ROLE_HEADS, key=len, reverse=True)
_UNIQUE_ROLE = re.compile(
    r"(?i)\b(?:the\s+(?:only|sole|one)|only|sole)\s+((?:[\w'-]+\s+){0,3}?)(?:"
    + "|".join(re.escape(head) for head in _ROLE_HEADS)
    + r")s?\s+(?:on|at|in|from|for|with|based|seconded|who)\b"
)
_NOT_DESCRIBING = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "of", "by", "to", "for", "with", "and", "or", "in", "on", "at",
    "from", "as", "if", "when", "be", "been", "is", "was", "are", "were", "one", "two", "each", "every", "any", "more",
    "than", "other", "per", "our", "your", "their", "his", "her", "my",
})
_RELATIVE = re.compile(r"(?i)\bwho\b")
_ROLE_TAIL_END = re.compile(
    r"[.,;:!?|\t\r\n\"()]|\s(?:is|was|are|were|has|had|have|will|would|can|cannot|could|should|may|might|must|does"
    r"|did|said|says|told)\b"
)


def unique_role_spans(text: str, tier: int = 1) -> tuple[DetectionFinding, ...]:
    out = []
    for match in _UNIQUE_ROLE.finditer(text):
        if any(word.casefold() in _NOT_DESCRIBING for word in match.group(1).split()):
            continue
        limit = min(len(text), match.end() + UNIQUE_ROLE_MAX_TAIL)
        stop = _ROLE_TAIL_END.search(text, match.end(), limit)
        if stop is None and _RELATIVE.search(text, match.start(), limit):
            # DD-26: a "who" clause still running at the cut goes on to the auxiliary verb that opens the main verb
            # ("... who grew up speaking Mandarin at home | has requested ...") or to the end of its cell or line, if
            # one is within ``UNIQUE_ROLE_MAX_RELATIVE`` characters. Prose punctuation there is no sign that the clause
            # ended before a main verb the list does not know ("who started in March in the back office signed up
            # with the union."), so then it is cut as before.
            longer = _ROLE_TAIL_END.search(text, match.end(), min(len(text), match.end() + UNIQUE_ROLE_MAX_RELATIVE))
            if longer is not None and (longer.group()[0].isspace() or longer.group() == "|"):
                stop = longer
        if stop is not None:
            end = stop.start()
        elif limit < len(text):
            end = max(text.rfind(" ", match.end(), limit + 1), match.end())
        else:
            end = limit
        while end > match.start() and text[end - 1].isspace():
            end -= 1
        out.append(finding(match.start(), end, Label.QUASI_ID, UNIQUE_ROLE_STAGE, tier))
    return tuple(out)


# v0.5 fix "quasi-cells" (from split F): a QUASI_ID finding inside one table cell that covers only part of it is
# widened to the whole cell. A role description in a table is one value ("First-year electrical apprentice, only
# apprentice on the night roster", "Longest-serving member, in the same flat since it opened"); on split F the finders
# marked half of two such cells and the other half was released beside it. Cells are found as in
# ``table_location_spans``: a header line without digits, then rows; a piece after an unquoted comma followed by a space
# belongs to the cell before it when that accounts for every extra cell. A finding that crosses a cell boundary, and a
# cell longer than ``QUASI_CELL_MAX_CHARS``, are left as they are. A ";" inside a cell ends a clause (DD-26, split G):
# the finding is widened to the clause (or clauses) it touches, not to the whole cell, so "the only female apprentice
# boilermaker enrolled at the Ararat trades workshop; welfare check requested by Youth Engagement Officer" keeps its
# second clause. PII-Tracer's QUASI_ID label is not widened: it marks single sensitive words ("chemo", "EpiPen") as
# quasi-identifiers, and on split D half C widening those took case numbers and site names in free-text cells.
# Document structure only; no vocabulary.
QUASI_CELL_STAGE = "quasi-cells"
QUASI_CELL_MAX_CHARS = 160
QUASI_CELL_NOT_FROM = "pii-tracer"


def quasi_cell_spans(
    text: str,
    findings: tuple[DetectionFinding, ...],
    tier: int = 1,
) -> tuple[DetectionFinding, ...]:
    quasi = [
        (item.start, item.end) for item in findings
        if any(e.label is Label.QUASI_ID and e.stage != QUASI_CELL_NOT_FROM for e in item.evidence)
    ]
    if not quasi:
        return ()
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    out, index = [], 0
    while index < len(lines):
        header = lines[index].rstrip("\r\n").lstrip("﻿")
        separator = max(SEPS, key=header.count)
        if header.count(separator) < (2 if separator == "|" else 1) or any(ch.isdigit() for ch in header):
            index += 1
            continue
        try:
            cells = _cells(header, separator)
        except (csv.Error, ValueError, StopIteration):
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
            if len(row_cells) < len(cells):
                break
            groups = []
            for at, (a, b) in enumerate(row_cells):
                if groups and row[row_cells[at - 1][1]:a][-1:].isspace():
                    groups[-1].append(at)
                else:
                    groups.append([at])
            if len(groups) != len(cells):
                groups = [[at] for at in range(len(row_cells))]
            for group in groups:
                a, b = row_cells[group[0]][0], row_cells[group[-1]][1]
                while a < b and (row[a].isspace() or row[a] == '"'):
                    a += 1
                while b > a and (row[b - 1].isspace() or row[b - 1] == '"'):
                    b -= 1
                if not a < b <= a + QUASI_CELL_MAX_CHARS:
                    continue
                start, end = offsets[row_index] + a, offsets[row_index] + b
                inside = [(s, e) for s, e in quasi if start <= s and e <= end and (s, e) != (start, end)]
                stops = [at for at in range(start, end) if text[at] == ";"] if inside else []
                widened = set()
                for s, e in inside:
                    # Only the clause of the cell that holds the finding (DD-26): a ";" inside a cell ends it.
                    lo = max([at + 1 for at in stops if at < s], default=start)
                    hi = min([at for at in stops if at >= e], default=end)
                    while lo < hi and text[lo].isspace():
                        lo += 1
                    while hi > lo and text[hi - 1].isspace():
                        hi -= 1
                    if (lo, hi) != (s, e):
                        widened.add((lo, hi))
                out.extend(finding(lo, hi, Label.QUASI_ID, QUASI_CELL_STAGE, tier) for lo, hi in sorted(widened))
            row_index += 1
        index = row_index if row_index > index + 1 else index + 1
    return tuple(out)


# v0.3 fix "address-line-numbers": scanned letters and statements often break an address after the street number,
# leaving the number alone on its own line ("48" then "EXAMPLE PDE" then "QUARRYBEND NSW 2999"). When an ADDRESS
# finding starts a line (only whitespace before it) and the previous non-empty line is only a street, unit or lot
# number, that number becomes part of the address evidence. Nothing else is added.
ADDRESS_LINE_STAGE = "address-lines"
LONE_STREET_NUMBER = re.compile(
    r"(?i)(?:(?:unit|u|lot|shop|suite|level|lvl|apt|flat)\s*)?[0-9]{1,5}[a-z]?(?:\s*[/\-]\s*[0-9]{1,5}[a-z]?)?"
)


def address_line_numbers(text: str, findings: tuple[DetectionFinding, ...]) -> tuple[DetectionFinding, ...]:
    added = []
    for item in findings:
        if Label.ADDRESS not in item.labels:
            continue
        line_start = max(text.rfind("\n", 0, item.start), text.rfind("\r", 0, item.start)) + 1
        if text[line_start:item.start].strip():
            continue
        cursor = line_start
        while cursor > 0 and text[cursor - 1] in "\r\n \t\u00a0":
            cursor -= 1
        previous_start = max(text.rfind("\n", 0, cursor), text.rfind("\r", 0, cursor)) + 1
        line = text[previous_start:cursor]
        stripped = line.strip()
        if stripped and LONE_STREET_NUMBER.fullmatch(stripped):
            a = previous_start + len(line) - len(line.lstrip())
            added.append(finding(a, a + len(stripped), Label.ADDRESS, ADDRESS_LINE_STAGE, item.provenance_tiers[-1]))
    return union_findings(findings, added) if added else findings


def _propagate_text(
    text: str,
    spans: tuple[DetectionFinding, ...],
    stage: str,
    tier: int,
) -> tuple[DetectionFinding, ...]:
    base = union_findings(spans, table_spans(text, stage, tier))
    if v02():
        base = union_findings(base, table_quasi_spans(text, tier))
    if fix_on("column-identifiers"):
        base = union_findings(base, table_identifier_spans(text, tier))
    if fix_on("location-columns"):
        base = union_findings(base, table_location_spans(text, tier))
    if (fix_on("sensitive-columns") or fix_on("union-roles") or fix_on("negative-statements")
            or fix_on("indirect-statements")):
        from .statements import (
            indirect_statement_spans, negative_statement_spans, table_statement_spans, union_role_spans,
        )

        if fix_on("sensitive-columns"):
            base = union_findings(base, table_statement_spans(text, tier))
        if fix_on("union-roles"):
            base = union_findings(base, union_role_spans(text, tier))
        if fix_on("negative-statements"):
            base = union_findings(base, negative_statement_spans(text, tier))
        if fix_on("indirect-statements"):
            base = union_findings(base, indirect_statement_spans(text, tier))
    if fix_on("unique-roles"):
        base = union_findings(base, unique_role_spans(text, tier))
    if fix_on("quasi-cells"):
        base = union_findings(base, quasi_cell_spans(text, base, tier))
    return union_findings(base, extra_spans(text, base, stage, tier))


# v0.4 fix "propagate-detection-view" (DD-19, review COR-002): name propagation and the table passes read the detection
# view, like the rules, the marking gate, PII-Tracer and the finders, so one zero-width space or soft hyphen in a table
# header or in a later mention no longer switches them off. Findings come in as source offsets and are mapped into the
# view first (the view characters that come from the source characters they cover); every span the passes make is
# mapped back with ``DetectionView.to_source``, which covers whole source characters and the invisible ones inside the
# span. The findings that came in are kept as they came in. Text the view leaves unchanged is read as it is.
def _in_view(starts: list[int], start: int, end: int) -> Span | None:
    """The view span of the view characters that come from source characters ``[start, end)``; None if none do."""
    first, last = bisect_left(starts, start), bisect_left(starts, end)
    return Span(first, last) if first < last else None


def _propagate_view(
    view: DetectionView,
    spans: tuple[DetectionFinding, ...],
    stage: str,
    tier: int,
) -> tuple[DetectionFinding, ...]:
    starts = [source[0] for source in view.offsets]
    mapped = []
    for item in spans:
        inside = _in_view(starts, item.start, item.end)
        if inside is None:  # only invisible characters: nothing in the view to read; kept as it came in below
            continue
        statement = item.statement_span
        if statement is not None:
            statement = _in_view(starts, statement.start, statement.end)
        mapped.append(replace(item, span=inside, statement_span=statement))
    found = []
    for item in _propagate_text(view.text, tuple(mapped), stage, tier):
        statement = item.statement_span
        if statement is not None:
            statement = Span(*view.to_source(statement.start, statement.end))
        found.append(replace(item, span=Span(*view.to_source(item.start, item.end)), statement_span=statement))
    return union_findings(spans, found)


def propagate(
    text: str,
    spans: tuple[DetectionFinding, ...],
    stage: str = "propagate-tier1",
    tier: int = 1,
) -> tuple[DetectionFinding, ...]:
    if fix_on("propagate-detection-view"):
        view = detection_view(text)
        if view.changed:
            return _propagate_view(view, spans, stage, tier)
    return _propagate_text(text, spans, stage, tier)
