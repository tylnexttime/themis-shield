"""Explicit tracer-label adapter, independent of gold and evaluation scoring code.

The supplied bundle did not include score.py's label mapping. This adapter is
therefore an explicit integration surface, not a claim of mapping parity.
Unknown labels fail closed instead of disappearing from the union.
"""

from __future__ import annotations

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
