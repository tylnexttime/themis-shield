"""Operator console regressions from the independent review of candidate 0.5 (FAB-03, FAB-07).

FAB-03: Replace, rebuild, Undo, Replace again sent the replaced value to the cloud in clear while the point read
"replaced": after the undo the review text still showed the removed placeholder, and replacing "it" again was taken as
confirming the gate's own placeholder. FAB-07: two accepted actions (200) refused the job on the next send. Synthetic
data only; the helpers are those of ``test_operator_undo_and_tripwire``.
"""

from __future__ import annotations

import json

from router_fakes import request
from test_operator_undo_and_tripwire import TEXT, TOKEN, _approve, _gate, _held, _review, _run


def test_fab_03_replace_undo_replace_again_never_sends_the_value(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        point = view["points"][0]
        base = f"/api/operator/{job}/points/{point['id']}"
        headers = {"X-Themis-Confirm": job}
        replaced = await operator.post(
            f"{base}/replace", json={"start": 0, "end": view["text"].index(" ships"), "category": "person"})
        assert replaced.status_code == 200
        await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)  # rebuilt with the placeholder
        (token,) = TOKEN.findall((await _review(operator, job))["text"])
        undone = await operator.post(f"{base}/undo", json={})
        assert undone.status_code == 200
        # The review text of the package that held the removed placeholder is gone until the requester re-sends.
        assert undone.json()["text"] is None
        stale = await operator.post(f"{base}/replace", json={"start": 0, "end": len(token), "category": "person"})
        assert stale.status_code != 200
        assert (await operator.post(f"{base}/not-personal", json={})).status_code != 200
        # Re-send: the package is rebuilt without the Operator's placeholder and the point is open again.
        await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        view = await _review(operator, job)
        assert view["text"] is not None and "Morrison" in view["text"] and view["unresolved"] == 1
        again = await operator.post(
            f"{base}/replace", json={"start": 0, "end": view["text"].index(" ships"), "category": "person"})
        assert again.status_code == 200
        await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        await _approve(operator, job)
        sent = await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        assert sent.status_code == 200, sent.text
        outbound = json.dumps(h.fake.requests[-1], ensure_ascii=False)
        assert "Morrison" not in outbound and "Blake" not in outbound

    _run(h, exercise)


TWO = "Blake Morrison ships the synthetic widget on Tuesday. Blake Morrison signs for it."


def test_fab_07a_a_weaker_category_over_a_gate_placeholder_is_refused_at_once(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        point = view["points"][0]
        base = f"/api/operator/{job}/points/{point['id']}"
        span = {"start": 0, "end": view["text"].index(" ships")}
        weaker = await operator.post(f"{base}/replace", json={**span, "category": "quasi-identifier"})
        assert weaker.status_code == 400 and weaker.json()["error"]["code"] == "REPLACEMENT_CATEGORY_WEAKER"
        assert h.register.get_details(job)["status"] != "Refused"
        # The same span with the placeholder's own category, or a stronger one, is accepted and the job goes on.
        stronger = await operator.post(f"{base}/replace", json={**span, "category": "identifier"})
        assert stronger.status_code == 200
        rebuilt = await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        assert rebuilt.headers["x-themis-reason"] not in {"REQUIRED_STAGE_FAILED", "OPERATOR_REPLACEMENT_NOT_APPLIED"}
        assert h.register.get_details(job)["status"] != "Refused"

    _run(h, exercise)


def test_fab_07b_undoing_the_first_of_two_same_value_replacements_is_refused_at_once(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator, TWO)
        first, second = view["points"][:2]
        text = view["text"]
        spans = ((0, text.index(" ships")), (text.index(". ") + 2, text.index(" signs")))
        for point, (start, end) in zip((first, second), spans):
            replaced = await operator.post(
                f"/api/operator/{job}/points/{point['id']}/replace",
                json={"start": start, "end": end, "category": "person"},
            )
            assert replaced.status_code == 200
        refused = await operator.post(f"/api/operator/{job}/points/{first['id']}/undo", json={})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "PLACEHOLDER_SHARED"
        # Undo the second (which reused the placeholder), then the first: both accepted, and the job is not refused.
        assert (await operator.post(f"/api/operator/{job}/points/{second['id']}/undo", json={})).status_code == 200
        assert (await operator.post(f"/api/operator/{job}/points/{first['id']}/undo", json={})).status_code == 200
        rebuilt = await client.post("/v1/chat/completions", json=request(TWO), headers={"X-Themis-Confirm": job})
        assert rebuilt.headers["x-themis-reason"] != "OPERATOR_REPLACEMENT_NOT_APPLIED"
        assert h.register.get_details(job)["status"] != "Refused"

    _run(h, exercise)
