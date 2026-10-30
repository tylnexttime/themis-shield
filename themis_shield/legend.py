"""The placeholder legend (v0.4, "keep the work possible"): what kind of value each placeholder hides.

A placeholder carries only a coarse class letter (``⟦I:3fa9c1⟧``). With the legend on, the protected request gains
one system message listing every placeholder it sends, with the type its finding's label gives: "a bank account
number", "a date of birth", "a person". The cloud model learns the type, never the value, and is asked to keep each
placeholder exactly as written so restoration stays exact. The legend is part of the protected request, so it is
checked for originals and approved with everything else the cloud sees.

Optional and off by default (``[legend] enabled``). It says only what the label says: never where a value came from,
how sure a detector was, or anything about other values. A sensitive statement is described generically ("a
sensitive statement about a person"); naming its category ("a statement about a person's health") tells the cloud a
special-category fact exists, so it needs its own switch (``[legend] sensitive_categories``), off by default.

A placeholder first sent in an earlier turn is described by its class only ("an identifier"): the vault keeps values,
not labels, and a class-level description is never wrong, only coarser.
"""

from __future__ import annotations

from typing import Mapping

from .placeholders import SENSITIVE_LABELS, TokenClass, parse_strict_token, token_class_for
from .types import Label

LABEL_PHRASES = {
    Label.PERSON: "a person",
    Label.STUDENT_ID: "a student number",
    Label.USI: "a unique student identifier",
    Label.TFN: "a tax file number",
    Label.MEDICARE: "a Medicare number",
    Label.ABN: "a business number",
    Label.DRIVER_LICENCE: "a driver licence number",
    Label.PASSPORT: "a passport number",
    Label.BANK_ACCOUNT: "a bank account number",
    Label.EMAIL: "an email address",
    Label.PHONE: "a phone number",
    Label.ADDRESS: "an address",
    Label.DATE_OF_BIRTH: "a date of birth",
    Label.OTHER_DATE: "a date",
    Label.URL_PERSONAL: "a personal web address",
    Label.QUASI_ID: "a detail that could identify a person together with others",
    Label.INTERNAL_ID: "an internal identifier",
    Label.NATIONAL_ID: "a national identity number",
    Label.PAYMENT_CARD: "a payment card number",
    Label.USERNAME: "a username",
}
CLASS_PHRASES = {
    TokenClass.PERSON: "a person",
    TokenClass.CONTACT: "a contact detail",
    TokenClass.ADDRESS: "an address",
    TokenClass.IDENTIFIER: "an identifier",
    TokenClass.DATE: "a date",
    TokenClass.STATEMENT: "a sensitive statement about a person",
    TokenClass.QUASI_IDENTIFIER: "a detail that could identify a person together with others",
}
# Used only with [legend] sensitive_categories = true. Each says what kind of thing is hidden, never that it is true
# of anyone: "a statement about a person's health", not "the person's health condition".
CATEGORY_PHRASES = {
    Label.HEALTH: "health",
    Label.DISABILITY: "disability",
    Label.CRIMINAL: "criminal record",
    Label.FAMILY_VIOLENCE: "experience of family violence",
    Label.UNION: "trade-union membership",
    Label.RELIGION: "religion",
    Label.SEXUALITY: "sexuality",
    Label.ETHNICITY: "ethnicity",
    Label.INDIGENOUS: "Aboriginal or Torres Strait Islander status",
}
HEADER = (
    "Some values in this conversation were replaced by placeholders before it was sent to you. Keep every "
    "placeholder exactly as written wherever you refer to its value, and do not guess the value. Each placeholder "
    "stands for:"
)

DEFAULT_LEGEND = {"enabled": False, "sensitive_categories": False}
_legend = dict(DEFAULT_LEGEND)


def configure_legend(enabled: bool | None = None, *, sensitive_categories: bool | None = None) -> dict:
    """Set the process-wide legend behaviour; ``None`` restores each default. Returns the active settings."""
    values = {
        "enabled": DEFAULT_LEGEND["enabled"] if enabled is None else enabled,
        "sensitive_categories": (
            DEFAULT_LEGEND["sensitive_categories"] if sensitive_categories is None else sensitive_categories
        ),
    }
    if any(type(value) is not bool for value in values.values()):
        raise ValueError("invalid [legend] setting")
    _legend.update(values)
    return dict(_legend)


def legend_settings() -> dict:
    return dict(_legend)


def describe(token: str, labels: frozenset[Label] | None, *, sensitive_categories: bool = False) -> str:
    """The legend's phrase for one placeholder; ``labels`` is None when only the class letter is known."""
    token_class = parse_strict_token(token).token_class
    if token_class == TokenClass.STATEMENT:
        categories = sorted(
            (label for label in labels or () if label in SENSITIVE_LABELS), key=lambda label: label.value,
        )
        if sensitive_categories and categories:
            names = [CATEGORY_PHRASES[label] for label in categories]
            joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            return f"a statement about a person's {joined}"
        return CLASS_PHRASES[token_class]
    # The label the placeholder was allocated for: the one whose class matches the token's.
    matching = sorted(
        (label for label in labels or () if label in LABEL_PHRASES and token_class_for(label) == token_class),
        key=lambda label: label.value,
    )
    if len(matching) == 1:
        return LABEL_PHRASES[matching[0]]
    return CLASS_PHRASES[token_class]  # unknown, or more than one label of the class: the class is never wrong


def legend_text(labels_by_token: Mapping[str, frozenset[Label] | None], *, sensitive_categories: bool = False) -> str:
    """The legend message for the placeholders sent, one line each in token order; empty when none were sent."""
    if not labels_by_token:
        return ""
    lines = [
        f"- {token}: {describe(token, labels, sensitive_categories=sensitive_categories)}"
        for token, labels in sorted(labels_by_token.items())
    ]
    return HEADER + "\n" + "\n".join(lines)
