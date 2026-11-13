"""The worked example of ``docs/OPERATOR-GUIDE.md`` (RC-07, second review of candidate 0.5), run against the code.

The review found four places where the first version of the example described behaviour the console does not show. The
example was rewritten to match the code, and this test walks it step by step so that it stays true. Invented ticket;
a scripted fake cloud model plays the trip-wire; PII-Tracer is a stand-in that reads "Blake" as a name and the local
finders find no name.
"""

from __future__ import annotations

from dataclasses import replace
import json
import re

from themis_shield.catalogues.tripwire import Tripwire
from themis_shield.register.review_records import UNREPLACED_REASON

from catalogue_fakes import TripwireCloud, plain_harness, with_cloud
from router_fakes import request
from test_operator_undo_and_tripwire import _review, _run

TICKET = "Ticket 4417. Blake ships the synthetic widget to Ward 7B on Tuesday. Ask Ng at the Corella depot before 6."
ROUNDS = [
    [(r"Ward 7B", "ADDRESS"), (r"Corella depot", "ADDRESS")],
    [(r"Tuesday", "date"), (r"before 6", "QUASI_ID")],
    [(r"\bNg\b", "PERSON"), (r"\bwi(?=dget)", "PERSON")],
]
GUIDE = "docs/OPERATOR-GUIDE.md"


class NoNames:
    def chat_json(self, model_role, messages, schema, max_tokens):
        return {"entities": []}


def trip_wire():
    seen = []

    def reply(body):
        text = body["messages"][1]["content"]
        seen.append(text)
        want = ROUNDS[len(seen) - 1] if len(seen) <= len(ROUNDS) else []
        residual = [{"start": m.start(), "end": m.end(), "placeholder": None, "category": category}
                    for pattern, category in want for m in re.finditer(pattern, text)]
        return json.dumps({"residual": residual, "stop": bool(residual)})

    return reply


def test_the_guide_still_tells_this_example():
    from pathlib import Path

    guide = (Path(__file__).resolve().parents[1] / GUIDE).read_text(encoding="utf-8")
    example = guide[guide.index("### Worked example"):guide.index("## Quick reference")]
    assert "> " + TICKET in example
    for phrase in ("NOT CONVERGED", '"paragraph 1, line 1"', "REPLACEMENT_TOO_SHORT", '"Ask Ng"', "inside\n\"widget\""):
        assert phrase in example, phrase


def test_the_operator_guide_worked_example(tmp_path):
    h = plain_harness(tmp_path, phrases=("Blake",))
    h.router.targets = replace(h.router.targets, llm_client=NoNames())
    with_cloud(h, tmp_path, TripwireCloud(trip_wire()))
    h.router.tripwire = Tripwire(512)
    h.router.operator_console_enabled = True
    h.router.operator_identity = "synthetic-operator"

    async def exercise(client, operator):
        first = await client.post("/v1/chat/completions", json=request(TICKET))
        job = first.headers["x-themis-job"]
        assert len(h.fake.tripwire_requests) == 3
        view = await _review(operator, job)
        text = view["text"]
        # What the gate found: "Blake" is a person placeholder, held for the detectors' disagreement.
        blake, ng = view["points"]
        assert blake["reason"] == "ASK:disagreement-empty-llm" and blake["kind"] == "placeholder"
        assert blake["why"] == "the detectors disagreed on whether this is a name"
        # Rounds 1 to 3: T1 addresses, T2 two quasi-identifiers, round 3 replaces nothing: NOT CONVERGED.
        loop = view["tripwire"]["before_human"]
        assert loop["converged"] is False
        assert [(r["round"], r["outcome"], len(r["added"])) for r in loop["rounds"]] == [
            (1, "corrected", 2), (2, "corrected", 2), (3, "not_converged", 0)]
        assert all(p["placeholder"].startswith("⟦A:") for p in view["gate_settled"] if p["round"] == 1)
        assert all(p["placeholder"].startswith("⟦Q:") for p in view["gate_settled"] if p["round"] == 2)
        for word in ("Ward 7B", "Corella depot", "Tuesday", "before 6"):
            assert word not in text
        # In the console: "wi" listed and highlighted inside "widget", not a point; two open points on line 1.
        items = loop["rounds"][-1]["items"]
        placed = [text[i["review_start"]:i["review_end"]] for i in items if i["review_start"] is not None]
        assert sorted(placed) == ["Ng", "wi"] and text[text.index("widget"):][:2] == "wi"
        assert ng["reason"] == UNREPLACED_REASON and text[ng["start"]:ng["end"]] == "Ng"
        assert [p["number"] for p in view["points"]] == [1, 2]
        assert {p["location"] for p in view["points"]} == {"paragraph 1, line 1"}
        refused = await operator.post(f"/api/operator/{job}/approve", json={"stage": 1, "reason": "ROUTINE_APPROVED"})
        assert refused.status_code == 409
        # Settling point 1: show the original, then confirm the gate's placeholder as it is: nothing changes.
        shown = await operator.post(f"/api/operator/{job}/points/{blake['id']}/reveal", json={})
        assert shown.json()["original"] == "Blake"
        confirmed = await operator.post(
            f"/api/operator/{job}/points/{blake['id']}/replace",
            json={"start": blake["start"], "end": blake["end"], "category": "person"})
        assert confirmed.status_code == 200 and confirmed.json()["package"] == "ready"
        assert len(h.fake.tripwire_requests) == 3
        # Point 2: "Ng" alone is refused; widened to "Ask Ng" it is accepted and the package goes back to building.
        base = f"/api/operator/{job}/points/{ng['id']}/replace"
        alone = await operator.post(base, json={"start": ng["start"], "end": ng["end"], "category": "person"})
        assert alone.status_code == 400 and alone.json()["error"]["code"] == "REPLACEMENT_TOO_SHORT"
        assert text[ng["start"] - 4:ng["end"]] == "Ask Ng"
        widened = await operator.post(base, json={"start": ng["start"] - 4, "end": ng["end"], "category": "person"})
        assert widened.status_code == 200 and widened.json()["package"] == "building"
        # The requester sends again: rebuilt, the loop runs again (one clean round), the earlier loop is folded.
        headers = {"X-Themis-Confirm": job}
        await client.post("/v1/chat/completions", json=request(TICKET), headers=headers)
        assert len(h.fake.tripwire_requests) == 4
        view = await _review(operator, job)
        assert view["tripwire"]["before_human"]["converged"] is True
        assert len(view["tripwire"]["before_human"]["earlier"]) == 1 and view["unresolved"] == 0
        for stage in (1, 2):
            approved = await operator.post(
                f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"})
            assert approved.status_code == 200, approved.text
        sent = await client.post("/v1/chat/completions", json=request(TICKET), headers=headers)
        assert sent.headers["x-themis-route"] == "cloud-tokenised"
        outbound = h.fake.requests[-1]["messages"][-1]["content"]
        for value in ("Blake", "Ward 7B", "Tuesday", "Ask Ng", "Corella", "before 6"):
            assert value not in outbound

    _run(h, exercise)
