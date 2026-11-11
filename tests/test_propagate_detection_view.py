"""v0.4 fix "propagate-detection-view" (DD-19, review COR-002): propagation reads the detection view.

Name propagation and the table name, identifier, quasi-identifier, location and statement passes used to read the raw
text, so one zero-width space or soft hyphen (common in text copied from a PDF) in a table header or in a later mention
switched that protection off. With the switch on they read the view and every span maps back to the source characters,
the invisible ones inside it included. Synthetic only: every name and number below is invented.
"""

from __future__ import annotations

import pytest

from themis_shield.detectors.contracts import finding
from themis_shield.detectors.propagate import propagate
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.types import Label

SWITCH = "propagate-detection-view"
ZWSP, SHY = "\u200b", "\u00ad"
ASTRAL = "\U0001F600"  # one code point, two UTF-16 units


def switch_off():
    configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != SWITCH])


def person(start, end, stage="rules"):
    return finding(start, end, Label.PERSON, stage, 1)


def values(text, seed=(), label=None):
    """The source text of every finding propagate makes (optionally of one label), in order."""
    return [text[f.start:f.end] for f in propagate(text, seed) if label is None or label in f.labels]


# ----------------------------------------------------------------------------------------------------------------
def test_the_switch_is_a_default_fix_after_location_columns():
    assert DETECTION_FIXES.index(SWITCH) > DETECTION_FIXES.index("location-columns")
    assert SWITCH in DEFAULT_DETECTION_FIXES and fix_on(SWITCH)
    switch_off()
    assert not fix_on(SWITCH)
    assert SWITCH in configure_detection_fixes("all")


# ----------------------------------------------------------------------------------------------------------------
# An invisible character inside a table header (the review's reproductions).
@pytest.mark.parametrize("invisible", [ZWSP, SHY])
def test_an_invisible_character_in_an_identifier_column_header(invisible):
    text = f"Name,Licen{invisible}ce Number,Site\nJane Doe,12345678,North\nJohn Roe,87654321,South\n"
    plain = text.replace(invisible, "")
    assert values(plain, label=Label.DRIVER_LICENCE) == ["12345678", "87654321"]
    assert values(text, label=Label.DRIVER_LICENCE) == ["12345678", "87654321"]
    switch_off()
    assert values(text, label=Label.DRIVER_LICENCE) == []


@pytest.mark.parametrize("invisible", [ZWSP, SHY])
def test_an_invisible_character_in_a_name_header(invisible):
    text = f"Na{invisible}me,Site\nMara Lindahl,North\nRavi Kumar,South\n"
    assert {"Mara Lindahl", "Ravi Kumar"} <= set(values(text, label=Label.PERSON))
    switch_off()
    assert values(text, label=Label.PERSON) == []


def test_an_invisible_character_in_a_quasi_identifier_table_header():
    text = f"dob,Si{ZWSP}te\n1970-01-01,North depot\n1971-01-01,South annex\n"
    assert values(text, label=Label.QUASI_ID) == ["North depot", "South annex"]
    switch_off()
    assert values(text, label=Label.QUASI_ID) == []


def test_an_invisible_character_in_a_residence_column_header():
    text = f"name,resi{ZWSP}dence_postcode,site\nMara,2741,North\nRavi,2743,South\n"
    assert values(text, label=Label.ADDRESS) == ["2741", "2743"]
    switch_off()
    assert values(text, label=Label.ADDRESS) == []


def test_an_invisible_character_in_a_sensitive_column_header_keeps_the_statement_span():
    text = f"name,Union{ZWSP} member\nMara,Yes\n"
    (item,) = [f for f in propagate(text, ()) if Label.UNION in f.labels]
    assert text[item.start:item.end] == "Yes"
    assert item.statement_span is not None and text[item.statement_span.start:item.statement_span.end] == "Yes"
    switch_off()
    assert values(text, label=Label.UNION) == []


def test_an_invisible_character_in_a_union_role_sentence():
    text = f"Mara is the shop stew{SHY}ard on B shift."
    (item,) = [f for f in propagate(text, ()) if Label.UNION in f.labels]
    assert text[item.statement_span.start:item.statement_span.end] == text[:-1]
    switch_off()
    assert values(text, label=Label.UNION) == []


def test_a_cell_with_an_invisible_character_inside_is_covered_whole():
    text = f"Name,Licen{ZWSP}ce Number\nJane Doe,1234{ZWSP}5678\n"
    assert values(text, label=Label.DRIVER_LICENCE) == [f"1234{ZWSP}5678"]


# ----------------------------------------------------------------------------------------------------------------
# An invisible or compatibility character inside a later mention of a name already found.
LATER = "Maria Lindahl called. Later {} said the roof was fine."


@pytest.mark.parametrize("mention", [
    f"Lind{ZWSP}ahl", f"Lind{SHY}ahl", "\uff2c\uff49\uff4e\uff44\uff41\uff48\uff4c",  # full-width "Lindahl"
])
def test_a_later_mention_with_an_invisible_or_compatibility_character_is_protected(mention):
    text = LATER.format(mention)
    seed = (person(0, len("Maria Lindahl")),)
    assert mention in values(text, seed, Label.PERSON)
    switch_off()
    assert mention not in values(text, seed, Label.PERSON)


def test_a_first_mention_found_with_an_invisible_character_inside_propagates_its_real_tokens():
    # The finding arrives in source coordinates: it covers the ZWSP. Read raw, the name's words would be "Ma" and
    # "ria", and the later "Maria" would stay in clear.
    text = f"Ma{ZWSP}ria Lindahl called. Later Maria agreed."
    seed = (person(0, len(f"Ma{ZWSP}ria Lindahl")),)
    assert "Maria" in values(text, seed, Label.PERSON)
    switch_off()
    assert "Maria" not in values(text, seed, Label.PERSON)


def test_the_stage_and_tier_reach_the_propagated_evidence():
    text = LATER.format(f"Lind{ZWSP}ahl")
    seed = (person(0, len("Maria Lindahl")),)
    later = [f for f in propagate(text, seed, "propagate-final", 2) if text[f.start:f.end] == f"Lind{ZWSP}ahl"]
    assert later and all(
        any(item.stage == "propagate-final" and item.tier == 2 for item in f.evidence) for f in later
    )


# ----------------------------------------------------------------------------------------------------------------
# Offsets stay exact.
def test_a_crlf_table_keeps_exact_offsets():
    text = f"Name,Licen{ZWSP}ce Number,Site\r\nJane Doe,12345678,North\r\nJohn Roe,87654321,South\r\n"
    found = [f for f in propagate(text, ()) if Label.DRIVER_LICENCE in f.labels]
    assert [text[f.start:f.end] for f in found] == ["12345678", "87654321"]
    assert [f.start for f in found] == [text.index("12345678"), text.index("87654321")]
    assert {"Jane Doe", "John Roe"} <= set(values(text, label=Label.PERSON))


def test_an_astral_character_before_the_table_keeps_exact_offsets():
    text = f"{ASTRAL} Staff list {ASTRAL}\nName,Licen{ZWSP}ce Number,Site\nJane {ASTRAL}Doe,12345678,North\n"
    found = {text[f.start:f.end]: (f.start, f.end) for f in propagate(text, ())}
    assert found["12345678"] == (text.index("12345678"), text.index("12345678") + 8)
    assert found[f"Jane {ASTRAL}Doe"] == (text.index("Jane"), text.index("Jane") + len(f"Jane {ASTRAL}Doe"))


def test_a_finding_that_starts_on_an_invisible_character_is_kept_as_it_came_in():
    text = f"{ZWSP}Maria Lindahl called. Later Maria agreed."
    seed = (person(0, len(f"{ZWSP}Maria Lindahl")),)
    found = propagate(text, seed)
    assert (0, len(f"{ZWSP}Maria Lindahl")) in [(f.start, f.end) for f in found]
    assert "Maria" in values(text, seed, Label.PERSON)[1:]


def test_every_span_made_from_the_view_lies_inside_the_source():
    text = f"{ASTRAL}{ZWSP}Name,Licen{SHY}ce Number\n{ZWSP}Jane Doe,{SHY}12345678{ZWSP}\n"
    found = propagate(text, ())
    assert found
    for f in found:
        assert 0 <= f.start < f.end <= len(text)
        if f.statement_span is not None:
            assert 0 <= f.statement_span.start < f.statement_span.end <= len(text)


# ----------------------------------------------------------------------------------------------------------------
# Text the view leaves unchanged gives byte-identical results, with the switch on or off.
PLAIN = (
    "Maria Lindahl called. Later Lindahl said M. L. agreed.\n"
    "Name,Licence Number,Site,dob\nJane Doe,12345678,North,1970-01-01\nJohn Roe,87654321,South,1971-01-01\n"
)


def test_text_the_view_leaves_unchanged_gives_identical_results():
    seed = (person(0, len("Maria Lindahl")),)
    on = propagate(PLAIN, seed)
    assert on and Label.DRIVER_LICENCE in {label for f in on for label in f.labels}
    switch_off()
    assert propagate(PLAIN, seed) == on


def test_with_the_switch_off_the_result_is_the_raw_text_result():
    text = LATER.format(f"Lind{ZWSP}ahl") + f"\nName,Licen{ZWSP}ce Number\nJane Doe,12345678\n"
    switch_off()
    # Exactly today's behaviour: the zero-width header hides the column, the split mention is not found.
    seed = (person(0, len("Maria Lindahl")),)
    assert values(text, seed, Label.DRIVER_LICENCE) == []
    assert f"Lind{ZWSP}ahl" not in values(text, seed)