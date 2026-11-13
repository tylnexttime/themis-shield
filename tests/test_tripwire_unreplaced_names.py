"""RC-06 (second review of candidate 0.5): a short or common-word name the cloud trip-wire flags is never left in clear
silently.

FAB-06 keeps short and common words out of the conversation vault (the trip-wire reads untrusted text; anything it
names and the loop vaults makes every later cloud answer containing those letters fail). That stands. But a capitalised
whole word the trip-wire flags ("Ng", "Will", "Page") may be a real name: it is now an open point for the Operator
(``TRIPWIRE_UNREPLACED``) that blocks approval until it is settled, instead of a leftover highlight. Lower-case words and
spans that cut a word stay listed only. Synthetic data only; a scripted fake cloud model plays the trip-wire.
"""

from __future__ import annotations

import json
import re

from themis_shield.catalogues.tripwire import MAX_OPEN_NAMES, Flag, Tripwire, unreplaced_name
from themis_shield.register.review_records import UNREPLACED_REASON

from router_fakes import request
from test_operator_undo_and_tripwire import _gate, _review, _run

TEXT = "Blake Morrison ships the synthetic widget. Ask Ng or Will Page about the night roster."


def flags(*patterns, category="person"):
    """A trip-wire reply that reports every match of the patterns, every round."""

    def reply(body):
        text = body["messages"][1]["content"]
        residual = [
            {"start": m.start(), "end": m.end(), "placeholder": None, "category": category}
            for pattern in patterns for m in re.finditer(pattern, text)
        ]
        return json.dumps({"residual": residual, "stop": bool(residual)})

    return reply


def _name_points(view):
    return [point for point in view["points"] if point["reason"] == UNREPLACED_REASON]


def test_which_findings_are_unreplaced_names():
    text = '"Ask Ng or Will Page about the night roster; see Corella."'
    at = lambda word, after=0: (text.index(word, after), text.index(word, after) + len(word))
    named = lambda span: unreplaced_name(text, Flag(*span, None, "person"))
    assert named(at("Ng")) and named(at("Will")) and named(at("Page"))
    assert not named(at("Corella"))  # plausible: the loop replaces it
    assert not named(at("the"))  # lower case: listed only
    assert not named((text.index("night"), text.index("night") + 2))  # "ni" cuts a word: listed only
    assert not unreplaced_name(text, Flag(None, None, "⟦P:000000⟧", "person"))


def test_short_names_are_open_points_that_block_approval_and_are_never_vaulted(tmp_path):
    h = _gate(tmp_path, tripwire=Tripwire(512), reply=flags(r"\bNg\b", r"\bWill\b", r"\bPage\b", r"ni(?=ght)"))

    async def exercise(client, operator):
        first = await client.post("/v1/chat/completions", json=request(TEXT))
        job = first.headers["x-themis-job"]
        view = await _review(operator, job)
        loop = view["tripwire"]["before_human"]
        assert loop["converged"] is False and loop["rounds"][-1]["added"] == []
        names = _name_points(view)
        assert sorted(view["text"][p["start"]:p["end"]] for p in names) == ["Ng", "Page", "Will"]
        assert all(p["kind"] == "text" and p["label"] == "PERSON" and p["resolution"] is None for p in names)
        assert all("trip-wire" in p["why"] for p in names)
        # "ni" inside "night" is listed for the Operator, never an open point; nothing flagged is vaulted (FAB-06).
        assert not any(view["text"][p["start"]:p["end"]] == "ni" for p in view["points"] if p["start"] is not None)
        values = set().union(*(h.router.vault.mapping(c).values() for c in list(h.router._conversations)))
        assert not values & {"Ng", "Will", "Page", "ni"}
        refused = await operator.post(f"/api/operator/{job}/approve", json={"stage": 1, "reason": "ROUTINE_APPROVED"})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "REVIEW_POINTS_UNRESOLVED"
        # A re-send of the same package keeps the points actionable.
        await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        view = await _review(operator, job)
        assert len(_name_points(view)) == 3 and view["text_available"]
        for point in view["points"]:
            settled = await operator.post(f"/api/operator/{job}/points/{point['id']}/not-personal", json={})
            assert settled.status_code == 200, settled.text
        for stage in (1, 2):
            approved = await operator.post(
                f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"})
            assert approved.status_code == 200, approved.text

    _run(h, exercise)


def test_a_lower_case_word_alone_stays_a_listed_leftover(tmp_path):
    h = _gate(tmp_path, tripwire=Tripwire(512), reply=flags(r"\bthe\b"))

    async def exercise(client, operator):
        first = await client.post("/v1/chat/completions", json=request(TEXT))
        view = await _review(operator, first.headers["x-themis-job"])
        assert view["tripwire"]["before_human"]["converged"] is False
        assert _name_points(view) == []

    _run(h, exercise)


def test_a_name_whose_place_is_not_exact_is_a_whole_document_point(tmp_path):
    """One round only, and that round replaced "Corella": the package changed after the trip-wire read it, so the place
    of "Ng" in the final package is not the one reported. The point is not placed by guess: it covers the document."""
    text = "Blake Morrison ships the synthetic widget to the Corella depot. Ask Ng about the night roster."
    h = _gate(tmp_path, tripwire=Tripwire(512, max_rounds=1), reply=flags(r"\bCorella\b", r"\bNg\b"))

    async def exercise(client, operator):
        first = await client.post("/v1/chat/completions", json=request(text))
        view = await _review(operator, first.headers["x-themis-job"])
        loop = view["tripwire"]["before_human"]
        assert loop["converged"] is False and loop["rounds"][-1]["added"]
        (point,) = _name_points(view)
        assert point["kind"] == "document" and point["start"] is None and point["location"] == "whole document"

    _run(h, exercise)


def test_a_flood_of_names_is_one_whole_document_point(tmp_path):
    names = ("Ng", "Li", "Jo", "Al", "Ed", "Bo", "Cy", "Di", "Ty")
    assert len(names) == MAX_OPEN_NAMES + 1
    text = "Blake Morrison ships the synthetic widget. Ask " + ", ".join(names) + " about it."
    h = _gate(tmp_path, tripwire=Tripwire(512), reply=flags(r"\b(?:" + "|".join(names) + r")\b"))

    async def exercise(client, operator):
        first = await client.post("/v1/chat/completions", json=request(text))
        view = await _review(operator, first.headers["x-themis-job"])
        (point,) = _name_points(view)
        assert point["kind"] == "document" and point["label"] is None

    _run(h, exercise)


def test_replacing_a_short_name_alone_is_refused_at_once_and_a_widened_span_works(tmp_path):
    """A two-letter value in the codebook would make the next package fail the exact-value check (its letters are in
    ordinary words) and the job be refused; "Replace it" refuses it when the Operator acts (REPLACEMENT_TOO_SHORT),
    and a span widened to a neighbouring word ("Ask Ng") is accepted and the job goes on."""
    h = _gate(tmp_path, tripwire=Tripwire(512), reply=flags(r"\bNg\b"))

    async def exercise(client, operator):
        first = await client.post("/v1/chat/completions", json=request(TEXT))
        job = first.headers["x-themis-job"]
        view = await _review(operator, job)
        (point,) = _name_points(view)
        base = f"/api/operator/{job}/points/{point['id']}"
        alone = await operator.post(
            f"{base}/replace", json={"start": point["start"], "end": point["end"], "category": "person"})
        assert alone.status_code == 400 and alone.json()["error"]["code"] == "REPLACEMENT_TOO_SHORT"
        assert view["text"][point["start"] - 4:point["end"]] == "Ask Ng"
        widened = await operator.post(
            f"{base}/replace", json={"start": point["start"] - 4, "end": point["end"], "category": "person"})
        assert widened.status_code == 200, widened.text
        again = await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        assert again.headers["x-themis-reason"] not in {"REQUIRED_STAGE_FAILED", "OPERATOR_REPLACEMENT_NOT_APPLIED"}
        assert h.register.get_details(job)["status"] != "Refused"
        view = await _review(operator, job)
        assert "Ng" not in view["text"] and view["tripwire"]["before_human"]["converged"] is True

    _run(h, exercise)
