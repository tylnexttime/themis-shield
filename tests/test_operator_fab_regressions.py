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
