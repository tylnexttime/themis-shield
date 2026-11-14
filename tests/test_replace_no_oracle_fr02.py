"""Review FR-02 (14/11/2026): "Replace it" over a placeholder says nothing about the hidden original.

Before, replacing a held person placeholder with another category answered REPLACEMENT_TOO_SHORT when the hidden
original was "Ng" or "Will" and INVALID_REPLACEMENT_SPAN when it was "Blake", with no reveal recorded: one bit about a
value the Operator had not revealed. Now every refusal that depends on a hidden original answers with one code,
REPLACEMENT_NOT_ACCEPTED, whatever its length or content, and changes nothing. Every name is invented.
"""

from __future__ import annotations

from test_operator_review import _dump, _gate, _held, _run

NAMES = ("Ng", "Will", "Blake")


def _answers(tmp_path, name):
    h = _gate(tmp_path, phrases=(name,))
    out = {}

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator, f"{name} ships the synthetic widget on Tuesday.")
        point = view["points"][0]
        assert view["text"][point["start"]:point["end"]].startswith("⟦")  # the original is hidden behind a placeholder
        before = _dump(tmp_path / "register.db")
        for category in view["categories"]:
            if category == "person":
                continue
            reply = await operator.post(f"/api/operator/{job}/points/{point['id']}/replace",
                                        json={"start": point["start"], "end": point["end"], "category": category})
            out[category] = (reply.status_code, reply.json().get("error", {}).get("code"))
        after = _dump(tmp_path / "register.db")
        out["_unchanged"] = before == after
        out["_reveals"] = after.get("review_reveals")

    _run(h, exercise)
    return out


def test_replace_over_a_placeholder_answers_the_same_whatever_the_hidden_original(tmp_path):
    answers = {}
    for name in NAMES:
        (tmp_path / name).mkdir()
        answers[name] = _answers(tmp_path / name, name)
        assert answers[name]["_unchanged"] and not answers[name]["_reveals"], name  # nothing settled, nothing recorded
    categories = [key for key in answers["Blake"] if not key.startswith("_")]
    assert categories
    for category in categories:
        codes = {answers[name][category] for name in NAMES}
        assert len(codes) == 1, (category, codes)  # one answer for "Ng", "Will" and "Blake"
        (status, code), = codes
        assert status == 400 and code != "REPLACEMENT_TOO_SHORT"
    seen = {answers["Blake"][category][1] for category in categories}
    assert "REPLACEMENT_NOT_ACCEPTED" in seen
