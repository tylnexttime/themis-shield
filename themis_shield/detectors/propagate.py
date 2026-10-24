"""Deterministic table, name-token and initials propagation.

"""

from __future__ import annotations

import csv
from dataclasses import replace
import re

from ..types import Label, Span
from .contracts import DetectionFinding, FindingEvidence, finding
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


def propagate(
    text: str,
    spans: tuple[DetectionFinding, ...],
    stage: str = "propagate-tier1",
    tier: int = 1,
) -> tuple[DetectionFinding, ...]:
    base = union_findings(spans, table_spans(text, stage, tier))
    return union_findings(base, extra_spans(text, base, stage, tier))
