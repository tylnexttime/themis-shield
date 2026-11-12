"""v0.4 fix "joined-terms" (DD-17): an institutional term joined to a personal identifier is not trimmed off it.

Split E staff number KWA-EMP-92315 was found whole, then the allow-list (the organisation acronym KWA) cut KWA off
the front and the gate substituted EMP-92315, leaving KWA- in clear. Synthetic only; this file is pure ASCII, so
every joiner below is built from its code point.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from convert_profile import convert  # noqa: E402

from themis_shield.config import DeploymentProfile, UseCasePolicy  # noqa: E402
from themis_shield.detectors import DetectionTargets, detect_batch  # noqa: E402
from themis_shield.detectors import settings  # noqa: E402
from themis_shield.detectors.allowlist import (  # noqa: E402
    _JOINERS, _joined, apply_allowlist, compile_profile, terms, trim,
)
from themis_shield.detectors.contracts import finding  # noqa: E402
from themis_shield.detectors.settings import (  # noqa: E402
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, configure_detection_fixes, configure_detection_version, fix_on,
)
from themis_shield.placeholders import TokenAllocator  # noqa: E402
from themis_shield.substitute import substitute  # noqa: E402
from themis_shield.types import Label, Span  # noqa: E402

from router_fakes import Tracer  # noqa: E402


# ----------------------------------------------------------------------------------------------------------------
# Helpers. The profile is the shape of split E: an organisation with the acronym KWA among its forms.
ORGANISATION = {
    "organisation": "Karratong Water Authority",
    "organisation_forms": ["Karratong Water", "KWA", "the Authority"],
}
OFF = [name for name in DEFAULT_DETECTION_FIXES if name != "joined-terms"]  # every default fix but this one
VALUE = "KWA-EMP-92315"
TEXT = "My staff number is KWA-EMP-92315 and I need my leave balance."


def _profile(**kwargs):
    return DeploymentProfile("synthetic", "1", (), (), **kwargs)


def literal_allow(*literals):
    return compile_profile(_profile(allowlist_literals=tuple(literals)))


def pattern_allow(*patterns):
    return compile_profile(_profile(allowlist_patterns=tuple(patterns)))


KWA = literal_allow("KWA")
CONVERTED = compile_profile(_profile(institution=convert(ORGANISATION)))


@contextmanager
def switch(selection):
    """Run with a fix selection (a list of names or "none"); the autouse fixture restores the defaults."""
    try:
        configure_detection_fixes(selection)
        yield
    finally:
        configure_detection_fixes(None)


def kept(text, value, allow=KWA, label=Label.INTERNAL_ID):
    """What survives of a finding over the first occurrence of value in text once the allow-list has run."""
    start = text.index(value)
    item = finding(start, start + len(value), label, "rules", 1)
    return [text[piece.start:piece.end] for piece in apply_allowlist(text, (item,), allow)]


def kept_on_and_off(text, value, allow=KWA, label=Label.INTERNAL_ID):
    on = kept(text, value, allow, label)
    with switch(OFF):
        off = kept(text, value, allow, label)
    return on, off


# ----------------------------------------------------------------------------------------------------------------
# The switch.
def test_the_switch_is_registered_documented_and_on_by_default():
    assert DETECTION_FIXES[DETECTION_FIXES.index("tracer-evidence-sanity") + 1] == "joined-terms"
    assert "joined-terms" in DEFAULT_DETECTION_FIXES and fix_on("joined-terms")
    assert "joined-terms" in settings.__doc__ and "DD-17" in settings.__doc__
    with switch(OFF):
        assert not fix_on("joined-terms") and fix_on("secret-references")
    with switch("none"):
        assert not fix_on("joined-terms")
    configure_detection_version("v0.1")  # the v0.1 control switches every fix off
    assert not fix_on("joined-terms")


# ----------------------------------------------------------------------------------------------------------------
# The split E format.
def test_a_staff_number_with_the_organisation_prefix_survives_whole():
    assert kept(TEXT, VALUE, CONVERTED) == [VALUE]
    assert kept(TEXT, VALUE, CONVERTED, Label.PERSON) == [VALUE]  # whatever the detector called it


def test_without_the_switch_the_prefix_is_trimmed_off_as_before():
    # Documents the old defect: KWA- stayed in clear beside the substituted rest.
    with switch(OFF):
        assert kept(TEXT, VALUE, CONVERTED) == ["EMP-92315"]
    with switch("none"):
        assert kept(TEXT, VALUE, CONVERTED) == ["EMP-92315"]
    configure_detection_version("v0.1")
    assert kept(TEXT, VALUE, CONVERTED) == ["EMP-92315"]


def test_the_organisation_own_mention_beside_the_number_is_still_allow_listed():
    text = "KWA staff number KWA-EMP-92315 is held by KWA."
    item = finding(text.index(VALUE), text.index(VALUE) + len(VALUE), Label.INTERNAL_ID, "rules", 1)
    assert [text[f.start:f.end] for f in apply_allowlist(text, (item,), KWA)] == [VALUE]
    bare = finding(0, 3, Label.INTERNAL_ID, "rules", 1)  # a finding over just the first KWA is removed
    assert apply_allowlist(text, (bare,), KWA) == ()


# ----------------------------------------------------------------------------------------------------------------
# Joiners.
JOINERS = [
    ("hyphen-minus", "-"), ("underscore", "_"), ("slash", "/"), ("backslash", chr(92)),
    ("U+2010 hyphen", chr(0x2010)), ("U+2011 non-breaking hyphen", chr(0x2011)), ("U+2012 figure dash", chr(0x2012)),
    ("U+2013 en dash", chr(0x2013)), ("U+2014 em dash", chr(0x2014)), ("U+2212 minus sign", chr(0x2212)),
    ("U+FF0D fullwidth hyphen-minus", chr(0xFF0D)),
]


def test_the_joiner_set_is_exactly_the_designed_one():
    assert _JOINERS == frozenset(joiner for _, joiner in JOINERS)
    assert len(JOINERS) == 11 and all(len(joiner) == 1 for _, joiner in JOINERS)
    for other in (" ", ",", ".", ":", ";", "(", ")", "[", "]", "|", "#", chr(39), chr(0xA0)):
        assert other not in _JOINERS


@pytest.mark.parametrize("joiner", [joiner for _, joiner in JOINERS], ids=[name for name, _ in JOINERS])
def test_every_joiner_keeps_the_value_whole(joiner):
    value = f"KWA{joiner}EMP{joiner}92315"
    text = f"Staff number {value} on file."
    for allow in (KWA, pattern_allow("KWA")):
        on, off = kept_on_and_off(text, value, allow)
        assert on == [value]
        if joiner == "_" and allow is KWA:
            # A literal term never matches next to an underscore (a word character), so it never trimmed this.
            assert off == [value]
        else:
            assert off == [f"EMP{joiner}92315"]  # the old defect, for every joiner


def test_a_suffix_and_an_infix_form_stay_whole():
    on, off = kept_on_and_off("Badge 92315-KWA issued.", "92315-KWA")
    assert on == ["92315-KWA"] and off == ["92315"]
    on, off = kept_on_and_off("Badge EMP-KWA-92315 issued.", "EMP-KWA-92315")
    assert on == ["EMP-KWA-92315"] and off == ["EMP", "92315"]
    on, off = kept_on_and_off("Badge EMP/KWA-92315 issued.", "EMP/KWA-92315")  # a different joiner each side
    assert on == ["EMP/KWA-92315"] and off == ["EMP", "92315"]


def test_a_term_joined_by_nothing_through_a_pattern_stays_whole():
    # A literal term never matches inside an alphanumeric run (the regex has a word-boundary guard); a pattern can.
    assert terms("Badge KWA92315 issued.", literal_allow("KWA")) == ()
    allow = pattern_allow("KWA")
    on, off = kept_on_and_off("Badge KWA92315 issued.", "KWA92315", allow)
    assert on == ["KWA92315"] and off == ["92315"]
    on, off = kept_on_and_off("Badge 92315KWA issued.", "92315KWA", allow)
    assert on == ["92315KWA"] and off == ["92315"]
    on, off = kept_on_and_off("Badge 92KWA315 issued.", "92KWA315", allow)
    assert on == ["92KWA315"] and off == ["92", "315"]


def test_a_longer_institutional_term_inside_the_value_is_joined_too():
    allow = literal_allow("KWA-EMP")
    on, off = kept_on_and_off(TEXT, VALUE, allow)
    assert on == [VALUE] and off == ["92315"]


# ----------------------------------------------------------------------------------------------------------------
# Not joined: trimmed exactly as today.
@pytest.mark.parametrize("text, value, expected", [
    ("Please ask Priya Kimura, KWA about it.", "Priya Kimura, KWA", ["Priya Kimura"]),
    ("Notes by Kimura KWA today.", "Kimura KWA", ["Kimura"]),
    ("Contact Kimura (KWA) today.", "Kimura (KWA)", ["Kimura"]),
    ("Contact KWA Kimura today.", "KWA Kimura", ["Kimura"]),
    ("See (KWA) only.", "(KWA)", []),
], ids=["comma", "space", "brackets", "space-before", "brackets-alone"])
def test_a_term_set_apart_by_space_or_punctuation_is_trimmed_as_today(text, value, expected):
    on, off = kept_on_and_off(text, value)
    assert on == off == expected


@pytest.mark.parametrize("separator", [" ", ", ", ". ", ":", ";", " (", ") ", " : ", "|", "#"],
                         ids=lambda s: repr(s))
def test_no_other_separator_joins(separator):
    value = f"KWA{separator}EMP-92315"
    text = f"Number {value} here."
    on, off = kept_on_and_off(text, value)
    assert on == off == ["EMP-92315"]


def test_a_joiner_with_nothing_alphanumeric_after_it_inside_the_span_joins_nothing():
    # KWA- as a whole span, -KWA as a whole span: today behaviour (the term is trimmed, nothing is left).
    for text, value in (("The prefix KWA- is used.", "KWA-"), ("The suffix -KWA is used.", "-KWA"),
                        ("The form KWA-- is used.", "KWA--"), ("The form KWA-/ is used.", "KWA-/")):
        on, off = kept_on_and_off(text, value)
        assert on == off == [], value
    # A character beyond the span never joins: the detector stopped at the hyphen, the number is outside.
    on, off = kept_on_and_off("The prefix KWA-92315 is used.", "KWA-")
    assert on == off == []
    # A term that does not overlap the finding at all never touches it.
    on, off = kept_on_and_off("The number KWA-92315 is used.", "92315")
    assert on == off == ["92315"]


def test_joined_unit():
    def check(text, start, end, term_start, term_end):
        return _joined(text, finding(start, end, Label.INTERNAL_ID, "rules", 1), term_start, term_end)

    assert check(VALUE, 0, 13, 0, 3)
    assert check("92315-KWA", 0, 9, 6, 9)
    assert check("KWA92315", 0, 8, 0, 3)
    assert check("EMP-KWA-92315", 0, 13, 4, 7)
    assert not check("KWA-", 0, 4, 0, 3)
    assert not check("-KWA", 0, 4, 1, 4)
    assert not check("KWA EMP", 0, 7, 0, 3)
    assert not check("KWA, EMP", 0, 8, 0, 3)
    assert not check("KWA-EMP-92315", 0, 3, 0, 3)  # the span is just the term: nothing inside the span to join
    # Only characters inside the span join: a detector span that stopped at the hyphen, the number is outside it.
    assert not check("KWA-92315", 0, 4, 0, 3)


# ----------------------------------------------------------------------------------------------------------------
# A term that covers the whole value still removes it; dates are untouched.
def test_a_term_covering_the_whole_span_still_removes_it():
    for allow, text, value in (
        (KWA, "The prefix KWA is used.", "KWA"),
        (KWA, "Staff KWA-EMP-92315 listed.", "KWA"),  # a detector found only the acronym
        (literal_allow("KWA-EMP-92315"), TEXT, VALUE),  # the whole value is the organisation own
        (literal_allow("KWA-EMP"), "Form KWA-EMP-92315 listed.", "KWA-EMP"),
        (literal_allow("KWA-EMP"), "Form KWA-EMP-92315 listed.", "EMP"),  # the term is wider than the span
    ):
        on, off = kept_on_and_off(text, value, allow)
        assert on == off == [], (text, value)


def test_date_intervals_keep_their_own_rules():
    allow = compile_profile(_profile(public_date_literals=("2027-03-01",)))
    text = "Dates 2027-03-01-2027-03-15 listed."
    on, off = kept_on_and_off(text, "2027-03-01-2027-03-15", allow, Label.OTHER_DATE)
    assert on == off == ["2027-03-15"]  # the public date is still cut out of a date range
    # A date interval never trims a non-date finding, with or without the switch.
    on, off = kept_on_and_off(text, "2027-03-01-2027-03-15", allow, Label.INTERNAL_ID)
    assert on == off == ["2027-03-01-2027-03-15"]


def test_phone_intervals_never_trim_a_generic_finding():
    # The public number written another way is a phone interval only (no term matches it): never trims, switch or not.
    allow = literal_allow("(02) 5550 4180")
    text = "Ref 12-02 5550 4180 filed."
    assert {kind for _, _, kind in terms(text, allow)} == {"phone"}
    on, off = kept_on_and_off(text, "12-02 5550 4180", allow)
    assert on == off == ["12-02 5550 4180"]


def test_a_public_number_written_as_listed_is_a_term_and_is_joined_like_any_other():
    allow = literal_allow("(02) 5550 4180")
    text = "Ref 12-(02) 5550 4180 filed."
    assert {kind for _, _, kind in terms(text, allow)} == {"phone", "term"}
    on, off = kept_on_and_off(text, "12-(02) 5550 4180", allow)
    assert on == ["12-(02) 5550 4180"] and off == ["12"]


def test_labels_that_are_never_trimmed_are_unchanged():
    on, off = kept_on_and_off("Her ID KWA-EMP-92315 is held.", VALUE, KWA, Label.QUASI_ID)
    assert on == off == [VALUE]


# ----------------------------------------------------------------------------------------------------------------
# End to end: the public detection entry point, then substitution.
class StaffNumberFinder:
    """A local-model stand-in that reads the staff number whole, as split E finders did."""

    def chat_json(self, model_role, messages, schema, max_tokens):
        content = "\n".join(message["content"] for message in messages)
        return {"entities": [{"label": "INTERNAL_ID", "value": VALUE}] if VALUE in content else []}


def _detect(text):
    policy = UseCasePolicy("s", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    profile = DeploymentProfile("s", "1", (), (policy,), institution=convert(ORGANISATION))
    targets = DetectionTargets(tracer=Tracer(), llm_client=StaffNumberFinder())
    return detect_batch([text], profile, targets, thorough=True)[0]


def test_detection_protects_all_thirteen_characters_of_the_staff_number():
    start = TEXT.index(VALUE)
    result = _detect(TEXT)
    covered = {i for f in result.findings for i in range(f.start, f.end)}
    assert set(range(start, start + 13)) <= covered
    substituted = substitute(TEXT, result.findings, TokenAllocator("synthetic"))
    assert [r.original_span for r in substituted.replacements] == [Span(start, start + 13)]
    assert "KWA" not in substituted.tokenised_text and "92315" not in substituted.tokenised_text


def test_detection_without_the_switch_leaves_the_prefix_in_clear():
    start = TEXT.index(VALUE)
    with switch(OFF):
        result = _detect(TEXT)
    spans = sorted((f.start, f.end) for f in result.findings)
    assert spans == [(start + 4, start + 13)]  # EMP-92315 only
    substituted = substitute(TEXT, result.findings, TokenAllocator("synthetic"))
    assert "KWA-" in substituted.tokenised_text


# ----------------------------------------------------------------------------------------------------------------
# Guard (in the spirit of DD-16): no gold value of the evaluation sets is ever shortened to a joined remainder.
# The evaluation runner takes its institution profile from a command-line flag (--institution-profile), not from
# the case file, so the mapping is not simple: split E file is checked against split E own profile (through
# eval/convert_profile.py, the evaluation only profile bridge); a file with no entry here is not checked.
GUARD_PROFILES = {"test-e-k.jsonl": ROOT / "eval" / "generator_e" / "half-k" / "profile.json"}


def _uncovered_and_joined(text, original, pieces):
    """Gaps between the original span and what trim() kept that hold an alphanumeric character and touch a kept
    piece through a joiner or directly: a joined remainder. A gap that ends in a space or punctuation is a cut."""
    bad = []
    previous = original.start
    for piece in pieces:
        gap = text[previous:piece.start]
        if gap and any(ch.isalnum() for ch in gap) and (gap[-1].isalnum() or gap[-1] in _JOINERS):
            bad.append((gap, text[piece.start:piece.end]))
        previous = piece.end
    if pieces:
        gap = text[previous:original.end]
        if gap and any(ch.isalnum() for ch in gap) and (gap[0].isalnum() or gap[0] in _JOINERS):
            bad.append((text[pieces[-1].start:pieces[-1].end], gap))
    return bad


def _guard(name, profile_path):
    allow = compile_profile(_profile(institution=convert(json.loads(profile_path.read_text(encoding="utf-8")))))
    checked, bad = 0, []
    for line in (ROOT / "eval" / "cases" / name).read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        text = case["text"]
        golds = [s for s in case["spans"] if any(ch in _JOINERS for ch in text[s["start"]:s["end"]])]
        if not golds:
            continue
        intervals = terms(text, allow)
        for gold in golds:
            checked += 1
            # A generic label: nothing in it exempts the value from the allow-list (a checksummed or sensitive
            # label would never be trimmed at all), so this is the hardest case for the rule.
            original = finding(gold["start"], gold["end"], Label.INTERNAL_ID, "gold", 1)
            kept_pieces = trim(original, intervals, text, allow.own_identifiers)
            for gap, rest in _uncovered_and_joined(text, original, kept_pieces):
                bad.append((case["id"], text[gold["start"]:gold["end"]], gap, rest))
    return checked, bad


def test_no_gold_value_of_the_evaluation_sets_is_shortened_to_a_joined_remainder():
    started = time.perf_counter()
    checked = 0
    for path in sorted(p for p in (ROOT / "eval" / "cases").glob("*.jsonl") if not p.name.endswith(".adjudicated.jsonl")):
        profile = GUARD_PROFILES.get(path.name)
        if profile is None:
            continue
        count, bad = _guard(path.name, profile)
        checked += count
        assert bad == [], path.name
    assert checked > 300  # split E: 549 gold values hold a joiner
    assert time.perf_counter() - started < 5


def test_the_guard_sees_the_defect_when_the_switch_is_off():
    # The guard has teeth: without the fix it finds the split E leaks (the staff-number prefix, 12 or more values).
    with switch(OFF):
        _, bad = _guard("test-e-k.jsonl", GUARD_PROFILES["test-e-k.jsonl"])
    assert len(bad) >= 12
    assert any(value.startswith("KWA-EMP-") and gap.startswith("KWA") for _, value, gap, _ in bad)