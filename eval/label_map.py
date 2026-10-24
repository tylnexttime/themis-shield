"""Explicit mapping from themis-shield's gate labels to the evaluation set's 27 gold labels.

The headline numbers (R_full, R_any, released leaks, hard-negative over-redaction) ignore labels entirely: a gold
span is protected when every letter and digit in it is covered by any prediction. This table only matters for the
typed and exact metrics and for per-label precision.

A gate label may stand for several gold labels (`score.py` semantics: the FIRST entry is the primary label that an
unmatched prediction is charged to in per-label precision). A finding that carries several gate labels is written as
one prediction whose label is the gate labels joined by "|"; it stands for the union of their gold labels, with the
primary gate label's primary gold label first.
"""
from __future__ import annotations

IDENTIFIER_GOLD = [
    "INTERNAL_ID", "TFN", "MEDICARE", "ABN", "NATIONAL_ID", "DRIVER_LICENCE", "PASSPORT", "BANK_ACCOUNT",
    "PAYMENT_CARD",
]

# gate label -> gold labels it may stand for (first = primary)
GATE_TO_EVAL: dict[str, list[str]] = {
    "PERSON": ["PERSON"],
    "USERNAME": ["USERNAME"],
    "EMAIL": ["EMAIL"],
    "PHONE": ["PHONE"],
    "ADDRESS": ["ADDRESS"],
    # v1 defines URL_PERSONAL as "a personal web or social media URL or handle": a handle is a USERNAME.
    "URL_PERSONAL": ["URL_PERSONAL", "USERNAME"],
    # STUDENT_ID also receives PII-Tracer's coarse `account_number` (labels.py), so it may stand for any
    # identifier number.
    "STUDENT_ID": IDENTIFIER_GOLD[:],
    "INTERNAL_ID": ["INTERNAL_ID"],
    # A national student identifier issued to a person.
    "USI": ["INTERNAL_ID", "NATIONAL_ID"],
    "TFN": ["TFN"],
    "MEDICARE": ["MEDICARE"],
    "ABN": ["ABN"],
    # The generic government-identifier label (prompt v2) covers tax, health-card and social-security numbers.
    "NATIONAL_ID": ["NATIONAL_ID", "TFN", "MEDICARE", "ABN"],
    "DRIVER_LICENCE": ["DRIVER_LICENCE"],
    "PASSPORT": ["PASSPORT"],
    "BANK_ACCOUNT": ["BANK_ACCOUNT"],
    "PAYMENT_CARD": ["PAYMENT_CARD"],
    "SECRET": ["SECRET"],
    # PII-Tracer's coarse `private_date` arrives as DATE_OF_BIRTH (labels.py: "conservative"), so a gate
    # DATE_OF_BIRTH may be any personal date.
    "DATE_OF_BIRTH": ["DATE_OF_BIRTH", "OTHER_DATE"],
    "OTHER_DATE": ["OTHER_DATE"],
    "QUASI_ID": ["QUASI_ID"],
    "HEALTH": ["HEALTH"],
    "DISABILITY": ["DISABILITY"],
    # The gate's INDIGENOUS label is the Aboriginal or Torres Strait Islander part of the gold ETHNICITY label.
    "INDIGENOUS": ["ETHNICITY"],
    "ETHNICITY": ["ETHNICITY"],
    "SEXUALITY": ["SEXUALITY"],
    "RELIGION": ["RELIGION"],
    "CRIMINAL": ["CRIMINAL"],
    "FAMILY_VIOLENCE": ["FAMILY_VIOLENCE"],
    "UNION": ["UNION"],
}

# Gold labels the gate's prompt-v1 finder has no label for (the finder cannot name them; a correct span can still
# be produced under another label, and the label-agnostic headline metrics count it).
UNNAMED_BY_PROMPT_V1 = {
    "USERNAME": "only as a 'handle' inside URL_PERSONAL",
    "INTERNAL_ID": "only STUDENT_ID (student numbers) and USI",
    "NATIONAL_ID": "no foreign national identifier label",
    "PAYMENT_CARD": "no payment-card label",
    "ETHNICITY": "only INDIGENOUS (Aboriginal or Torres Strait Islander status)",
    "UNION": "no trade-union label",
}


def combined_key(labels) -> str:
    """Prediction label for a finding with several gate labels: primary first, the rest sorted."""
    labels = list(labels)
    if not labels:
        raise ValueError("finding without labels")
    return "|".join(labels)


def labelset_for(keys) -> dict[str, list[str]]:
    """score.py labelset covering every gate label plus each combined key seen in the predictions."""
    table = {key: value[:] for key, value in GATE_TO_EVAL.items()}
    for key in keys:
        if key in table or "|" not in key:
            continue
        merged: list[str] = []
        for part in key.split("|"):
            for gold in GATE_TO_EVAL.get(part, []):
                if gold not in merged:
                    merged.append(gold)
        table[key] = merged
    return table
