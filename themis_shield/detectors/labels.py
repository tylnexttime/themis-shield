"""Explicit tracer-label adapter, independent of gold and evaluation scoring code.

The supplied bundle did not include score.py's label mapping. This adapter is
therefore an explicit integration surface, not a claim of mapping parity.
Unknown labels fail closed instead of disappearing from the union.
"""

from __future__ import annotations

from dataclasses import replace
import re

from ..types import Label


TRACER_ALIASES = {
    # PII-Tracer's own nine labels (HF model card), mapped to the primary native label exactly as the evaluation
    # harness did (first entry wins): dates are treated as
    # dates of birth (conservative), account numbers as student IDs, other_pii as a quasi-identifier.
    "private_person": Label.PERSON,
    "private_date": Label.DATE_OF_BIRTH,
    "private_url": Label.URL_PERSONAL,
    "private_address": Label.ADDRESS,
    "account_number": Label.STUDENT_ID,
    "private_email": Label.EMAIL,
    "private_phone": Label.PHONE,
    "other_pii": Label.QUASI_ID,
    "secret": Label.SECRET,
    "person": Label.PERSON,
    "person_name": Label.PERSON,
    "name": Label.PERSON,
    "email_address": Label.EMAIL,
    "phone_number": Label.PHONE,
    "telephone_number": Label.PHONE,
    "street_address": Label.ADDRESS,
    "postal_address": Label.ADDRESS,
    "birth_date": Label.DATE_OF_BIRTH,
    "dob": Label.DATE_OF_BIRTH,
    "date": Label.OTHER_DATE,
    "url": Label.URL_PERSONAL,
    "personal_url": Label.URL_PERSONAL,
    "student_number": Label.STUDENT_ID,
    "tax_file_number": Label.TFN,
    "medicare_number": Label.MEDICARE,
    "australian_business_number": Label.ABN,
    "driver_license": Label.DRIVER_LICENCE,
    "driver_license_number": Label.DRIVER_LICENCE,
    "driver_licence_number": Label.DRIVER_LICENCE,
    "passport_number": Label.PASSPORT,
    "bank_account_number": Label.BANK_ACCOUNT,
    "password": Label.SECRET,
    "api_key": Label.SECRET,
}


# v0.2 date normalisation. PII-Tracer has a single "private_date" label, which the adapter maps to DATE_OF_BIRTH
# (conservative). Some of those spans cannot be a date of birth, and as DATE_OF_BIRTH they are never allow-listed
# and count as disagreement evidence. They become OTHER_DATE (still redacted unless the allow-list cancels them):
# - a span with a time of day ("10:31 AM", "Wednesday, 1 February 2027 9:14 AM"): a time stamp, not a birth date;
# - a bare weekday name ("Tue");
# - a span holding one of the organisation's public dates (institution profile `public_dates`).
TIME_OF_DAY = re.compile(
    r"(?i)(?<![\w.:/])(?:[01]?\d|2[0-3])(?::[0-5]\d){1,2}(?:\s*[ap]\.?\s?m\b\.?)?(?![\w:/])"
    r"|(?<![\w.:/])(?:1[0-2]|0?[1-9])(?:[.:][0-5]\d)?\s*[ap]\.?\s?m\b\.?"
)
BARE_WEEKDAY = re.compile(
    r"(?i)(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)\.?"
)
_EDGE = " \t\r\n,;:.()[]"


def is_bare_time_or_weekday(value: str) -> bool:
    """The whole value is a weekday name or a time of day, with nothing that could date a person."""
    value = value.strip(_EDGE)
    return bool(value) and bool(BARE_WEEKDAY.fullmatch(value) or TIME_OF_DAY.fullmatch(value))


def normalise_tracer_dates(text: str, findings, public_dates=()) -> tuple:
    """Relabel tracer DATE_OF_BIRTH spans that cannot be a date of birth as OTHER_DATE (labels and evidence)."""
    publics = tuple(
        re.compile(r"(?<![0-9])" + re.escape(value) + r"(?![0-9])")
        for value in public_dates if isinstance(value, str) and value
    )
    out = []
    for item in findings:
        if item.labels == frozenset({Label.DATE_OF_BIRTH}) and all(
            e.stage == "pii-tracer" for e in item.evidence
        ):
            value = text[item.start:item.end]
            if (
                TIME_OF_DAY.search(value) or is_bare_time_or_weekday(value)
                or any(pattern.search(value) for pattern in publics)
            ):
                item = replace(
                    item, labels=frozenset({Label.OTHER_DATE}), primary_label=Label.OTHER_DATE,
                    evidence=tuple(replace(e, label=Label.OTHER_DATE) for e in item.evidence),
                )
        out.append(item)
    return tuple(out)


def native_tracer_label(value: object) -> Label:
    if not isinstance(value, str):
        raise ValueError("invalid-tracer-label")
    canonical = value.strip().replace("-", "_").replace(" ", "_").casefold()
    native = {label.value.casefold(): label for label in Label}
    if canonical in native:
        return native[canonical]
    if canonical in TRACER_ALIASES:
        return TRACER_ALIASES[canonical]
    # Do not put the unknown string in an exception, status or diagnostic.
    raise ValueError("unmapped-tracer-label")
