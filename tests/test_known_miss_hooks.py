"""Known-miss final sweep on the Claude Code hooks path (v0.3), against a real loopback server.

Every release is swept, re-submissions included: a package approved before a known-miss entry started matching is
refused (as on the web path); one not yet approved now needs a person; tool output with a known miss stops the job.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from themis_shield.catalogues.gate import KnownMissSweep

from catalogue_fakes import MISSED, known_miss
from test_hooks_live import SESSION, cli, live, read_response  # noqa: F401  (fixture)
from test_known_miss import add_missed

pytestmark = pytest.mark.usefixtures("au_pspf_markings")

PROMPT = f"Please check archive reference {MISSED} for the synthetic audit."


def sweeping(h, tmp_path):
    h.kmr = known_miss(tmp_path)
    h.router.known_miss = KnownMissSweep(h.kmr, timedelta(days=7))
    return h


def submit(h, prompt=PROMPT):
    return cli(h, "UserPromptSubmit", prompt=prompt)


def job_id(h):
    return next(reversed(h.app.state.hooks.sessions[SESSION].jobs.values()))


def test_resubmitting_an_approved_prompt_after_an_entry_matches_is_refused(live, tmp_path):
    sweeping(live, tmp_path)
    code, held, _ = submit(live)
    assert code == 0 and held["decision"] == "block"
    identifier = job_id(live)
    live.register.approve(identifier)
    assert submit(live)[1] == {}  # released once, before anyone knew
    add_missed(live.kmr)
    code, blocked, _ = submit(live)
    assert code == 0 and blocked["decision"] == "block"
    assert blocked["reason"].startswith("KNOWN_MISS_HIT") and identifier in blocked["reason"]
    assert MISSED not in blocked["reason"]
    assert live.register.get_job(identifier).status.value == "Refused"
    assert submit(live)[1]["decision"] == "block"  # stays closed


def test_a_prompt_not_yet_approved_now_needs_a_person(live, tmp_path):
    sweeping(live, tmp_path)
    submit(live)
    identifier = job_id(live)
    assert live.register.get_details(identifier)["confirmation_required"] == 0
    add_missed(live.kmr)
    code, held, _ = submit(live)
    assert code == 0 and held["decision"] == "block" and "held for operator approval" in held["reason"]
    assert live.register.get_details(identifier)["confirmation_required"] == 1
    assert live.register.get_job(identifier).status.value != "Refused"


def test_a_hit_on_first_submission_holds(live, tmp_path):
    sweeping(live, tmp_path)
    add_missed(live.kmr)
    code, held, _ = submit(live)
    assert code == 0 and held["decision"] == "block"
    assert live.register.get_details(job_id(live))["confirmation_required"] == 1


def test_tool_output_with_a_known_miss_stops_the_job(live, tmp_path):
    sweeping(live, tmp_path)
    clean = "What is a retention schedule?"
    submit(live, clean)
    identifier = job_id(live)
    live.register.approve(identifier)
    assert submit(live, clean)[1] == {}
    add_missed(live.kmr)
    code, response, _ = cli(
        live, "PostToolUse", tool_name="Read",
        tool_response=read_response(live.workspace / "notes.txt", f"Archive reference {MISSED}."),
    )
    assert code == 0 and response["decision"] == "block" and response["reason"].startswith("KNOWN_MISS_HIT")
    assert live.register.get_job(identifier).status.value == "Refused"
