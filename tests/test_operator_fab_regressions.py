"""Operator console regressions from the independent reviews of candidate 0.5 (FAB-03, FAB-07, RC-04, RC-05).

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


def test_fab_10_reveal_is_refused_once_the_job_is_closed(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        base = f"/api/operator/{job}/points/{view['points'][0]['id']}"
        assert (await operator.post(f"{base}/reveal", json={})).status_code == 200  # open: allowed
        assert (await operator.post(f"{base}/stop", json={})).status_code == 200
        refused = await operator.post(f"{base}/reveal", json={})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "REVIEW_CLOSED"
        assert "original" not in refused.json()

    _run(h, exercise)


# ----------------------------------------------------------------------------------------------------------------
# RC-04 (second review): the undo was not atomic. Between its register write and its drop of the stale review text, a
# "Replace it" on the placeholder (a second Operator tab) passed both FAB-03 guards and was taken as "confirm as it
# is", and the value later went out in clear. Now one lock per job covers undo, replace and not-personal, and an undo
# that removes a placeholder marks the text stale before it writes the register. The interleave is forced by wrapping
# ``register.undo_point``, as in the reviewer's reproduction.
async def _replaced_and_rebuilt(h, client, operator):
    job, view = await _held(client, operator)
    point = view["points"][0]
    base = f"/api/operator/{job}/points/{point['id']}"
    replaced = await operator.post(
        f"{base}/replace", json={"start": 0, "end": view["text"].index(" ships"), "category": "person"})
    assert replaced.status_code == 200
    await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
    (token,) = TOKEN.findall((await _review(operator, job))["text"])
    return job, point["id"], base, token


async def _never_sent_in_clear(h, client, operator, job):
    """Re-send: the point is open on fresh text, approval is refused, and nothing with the value goes out."""
    headers = {"X-Themis-Confirm": job}
    await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
    view = await _review(operator, job)
    assert view["text"] is not None and view["unresolved"] == 1
    assert view["points"][0]["resolution"] is None
    refused = await operator.post(f"/api/operator/{job}/approve", json={"stage": 1, "reason": "ROUTINE_APPROVED"})
    assert refused.status_code != 200
    await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
    assert all("Morrison" not in json.dumps(sent, ensure_ascii=False) for sent in h.fake.requests)


def test_rc_04_a_replace_from_another_thread_waits_for_the_undo_and_is_then_refused(tmp_path):
    import threading

    from themis_shield import review as reviews

    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, point_id, base, token = await _replaced_and_rebuilt(h, client, operator)
        real = h.router.register.undo_point
        seen = {}

        def other_tab(action):
            try:
                if action == "replace":
                    reviews.replace(h.router, job, point_id, 0, len(token), "person", actor="synthetic-operator")
                else:
                    reviews.not_personal(h.router, job, point_id, actor="synthetic-operator")
                seen[action] = "accepted"
            except Exception as failure:  # noqa: BLE001 - the code is what is asserted
                seen[action] = getattr(failure, "code", repr(failure))

        threads = []

        def undo_point(*args, **kwargs):
            for action in ("replace", "not-personal"):
                thread = threading.Thread(target=other_tab, args=(action,), daemon=True)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join(0.3)
                assert thread.is_alive(), "a point action ran inside the undo"  # blocked on the job's lock
            return real(*args, **kwargs)

        h.router.register.undo_point = undo_point
        try:
            undone = await operator.post(f"{base}/undo", json={})
        finally:
            h.router.register.undo_point = real
        assert undone.status_code == 200, undone.text
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        assert seen == {"replace": "REVIEW_REBUILD_REQUIRED", "not-personal": "REVIEW_REBUILD_REQUIRED"}
        await _never_sent_in_clear(h, client, operator, job)

    _run(h, exercise)


def test_rc_04_the_reviewers_interleave_inside_the_undo_is_refused(tmp_path):
    """The reviewer's ``test_c``: the replace lands inside ``undo_point`` itself (same thread, so the lock is
    re-entered); the review text is already stale there, so it is refused."""
    from themis_shield import review as reviews

    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, point_id, base, token = await _replaced_and_rebuilt(h, client, operator)
        real = h.router.register.undo_point
        seen = {}

        def undo_point(*args, **kwargs):
            for when in ("before", "after"):
                if when == "after":
                    out = real(*args, **kwargs)
                try:
                    reviews.replace(h.router, job, point_id, 0, len(token), "person", actor="synthetic-operator")
                    seen[when] = "accepted"
                except Exception as failure:  # noqa: BLE001
                    seen[when] = getattr(failure, "code", repr(failure))
            return out

        h.router.register.undo_point = undo_point
        try:
            undone = await operator.post(f"{base}/undo", json={})
        finally:
            h.router.register.undo_point = real
        assert undone.status_code == 200, undone.text
        assert seen == {"before": "REVIEW_REBUILD_REQUIRED", "after": "REVIEW_REBUILD_REQUIRED"}
        await _never_sent_in_clear(h, client, operator, job)

    _run(h, exercise)


def test_rc_04_a_failed_undo_leaves_the_review_as_it_was(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, point_id, base, token = await _replaced_and_rebuilt(h, client, operator)
        real = h.router.register.undo_point

        def undo_point(*args, **kwargs):
            raise RuntimeError("synthetic register failure")

        h.router.register.undo_point = undo_point
        try:
            failed = await operator.post(f"{base}/undo", json={})
        finally:
            h.router.register.undo_point = real
        assert failed.status_code != 200
        view = await _review(operator, job)
        assert view["text"] is not None and token in view["text"]  # not stale: the undo did not happen
        assert view["points"][0]["resolution"]["action"] == "replaced"
        assert (await operator.post(f"{base}/undo", json={})).status_code == 200  # and it can be retried

    _run(h, exercise)


# ----------------------------------------------------------------------------------------------------------------
# RC-05 (second review): "Not personal" needed no review text on screen, only an in-memory stale flag, so after a
# restart (or once the conversation expired) a point was settled blind. It now needs the current review text, like
# "Replace it" and reveal, and the refusal is value-free. The restart is simulated by clearing what lives in memory.
def _restart(router):
    with router._state_lock:
        for name in ("_reviews", "_review_stale", "_operator_points", "_operator_tokens", "_review_locks"):
            getattr(router, name).clear()


def test_rc_05_not_personal_is_refused_when_the_review_text_is_not_on_screen(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        base = f"/api/operator/{job}/points/{view['points'][0]['id']}"
        _restart(h.router)
        view = await _review(operator, job)
        assert view["text"] is None and view["text_available"] is False and view["unresolved"] == 1
        refused = await operator.post(f"{base}/not-personal", json={})
        assert refused.status_code == 404
        assert refused.json() == {"error": refused.json()["error"]}  # nothing but the error
        assert refused.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        assert "Blake" not in refused.text and "Morrison" not in refused.text
        assert (await _review(operator, job))["unresolved"] == 1
        # The requester sends again: the text is rebuilt, on screen, and the point can be settled.
        await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        assert (await _review(operator, job))["text_available"] is True
        settled = await operator.post(f"{base}/not-personal", json={})
        assert settled.status_code == 200 and settled.json()["unresolved"] == 0

    _run(h, exercise)


def test_rc_05_the_reviewers_stale_undo_then_restart_is_refused(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, point_id, base, token = await _replaced_and_rebuilt(h, client, operator)
        assert (await operator.post(f"{base}/undo", json={})).status_code == 200
        _restart(h.router)  # the stale flag is gone with the rest of the memory
        refused = await operator.post(f"{base}/not-personal", json={})
        assert refused.status_code == 404 and refused.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        await _never_sent_in_clear(h, client, operator, job)

    _run(h, exercise)


def test_rc_05_the_console_disables_not_personal_without_the_text():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "themis_shield" / "web" / "static" / "app.js").read_text(
        encoding="utf-8")
    start = script.index('button("Not personal: leave it"')
    end = script.index('button("Replace it"', start)
    assert "{ disabled: !review.text_available }" in script[start:end]
